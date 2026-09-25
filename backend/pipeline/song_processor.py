import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, Callable, Optional

from backend.config import SONGS_DIR, DEMUCS_MODEL, WHISPER_MODEL_SIZE, DEVICE, COMPUTE_TYPE
from backend.pipeline import local_media
from backend.pipeline.downloader import YouTubeDownloader
from backend.pipeline.separator import VocalSeparator
from backend.pipeline.lyrics_aligner import LyricsAligner
from backend.pipeline.loudness import analyze_audio_file
from backend.pipeline.pitch_extractor import PitchExtractor
from backend.services.local_import import LOCAL_URL_PREFIX, sidecar_lrc

logger = logging.getLogger("KaraTube.SongProcessor")

class SongProcessor:
    def __init__(self, whisper_model: Optional[str] = None, demucs_model: Optional[str] = None,
                 loudness_target_lufs: float = -14.0,
                 local_library: Optional[Any] = None):
        # 模型可由系統設定頁指定；沒指定就用環境變數／內建預設。
        # 模型是在建構時決定的，改設定要重開伺服器才生效（設定頁上有標註）。
        self.whisper_model = whisper_model or WHISPER_MODEL_SIZE
        self.demucs_model = demucs_model or DEMUCS_MODEL
        self.loudness_target_lufs = loudness_target_lufs
        # 本機匯入的帳本（`LocalImportLibrary`）。沒給就只有 YouTube 那條路 ——
        # 測試與純批次伺服器用得到。
        self.local_library = local_library
        self.downloader = YouTubeDownloader(output_dir=SONGS_DIR)
        self.separator = VocalSeparator(model_name=self.demucs_model, device=DEVICE)
        self.lyrics_aligner = LyricsAligner(model_size=self.whisper_model, device=DEVICE,
                                            compute_type=COMPUTE_TYPE)
        self.pitch_extractor = PitchExtractor()

    def is_song_ready(self, song_id: str) -> bool:
        """Check if all assets for this song already exist in cache."""
        song_dir = SONGS_DIR / song_id
        if not song_dir.exists():
            return False

        has_audio = (song_dir / "instrumental.mp3").exists() and (song_dir / "vocals.mp3").exists()
        has_lyrics = (song_dir / "lyrics.json").exists()
        has_meta = (song_dir / "metadata.json").exists()
        return has_audio and has_lyrics and has_meta

    async def process_song(
        self,
        url_or_id: str,
        progress_callback: Optional[Callable[[str, str, int], None]] = None
    ) -> Dict[str, Any]:
        """
        Execute the full pipeline asynchronously.
        progress_callback(song_id, status_message, percentage)

        來源有兩條：YouTube（`url` 或 11 碼 id）與本機檔案（`local:<song_id>`）。
        兩條路在「拿到 original_audio.mp3」之後就合流 —— 分離、對詞、音高、響度
        完全共用，所以匯入的歌跟點播的歌在系統裡沒有任何差別。
        """
        if isinstance(url_or_id, str) and url_or_id.startswith(LOCAL_URL_PREFIX):
            return await self.process_local_song(
                url_or_id[len(LOCAL_URL_PREFIX):], progress_callback=progress_callback)

        # Resolve song_id / url
        if "youtube.com" in url_or_id or "youtu.be" in url_or_id:
            url = url_or_id
            # Extract video id
            import re
            match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11})', url)
            song_id = match.group(1) if match else "temp_" + str(abs(hash(url)) % 1000000)
        else:
            song_id = url_or_id
            url = f"https://www.youtube.com/watch?v={song_id}"

        song_dir = SONGS_DIR / song_id
        song_dir.mkdir(parents=True, exist_ok=True)

        meta_file = song_dir / "metadata.json"

        # Check cache
        if self.is_song_ready(song_id):
            logger.info(f"Song {song_id} already fully processed and cached.")
            if progress_callback:
                progress_callback(song_id, "Ready (Cached)", 100)
            import json
            with open(meta_file, 'r', encoding='utf-8') as f:
                return json.load(f)

        try:
            # 1. Download
            if progress_callback:
                progress_callback(song_id, "Downloading YouTube Video & Audio...", 15)
            logger.info(f"[{song_id}] Downloading...")
            loop = asyncio.get_event_loop()
            meta = await loop.run_in_executor(None, self.downloader.download, url, song_id)

            audio_path = Path(meta["audio_path"])

            return await self._finish_song(song_id, song_dir, audio_path, meta,
                                           progress_callback=progress_callback)

        except Exception as e:
            logger.exception(f"Pipeline error on song {song_id}: {e}")
            if progress_callback:
                progress_callback(song_id, f"Error: {str(e)}", -1)
            raise e

    async def _finish_song(
        self,
        song_id: str,
        song_dir: Path,
        audio_path: Path,
        meta: Dict[str, Any],
        progress_callback: Optional[Callable[[str, str, int], None]] = None,
        local_lrc: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        拿到 `original_audio.mp3` 之後的共同流程：分離 → 對詞 → 音高 → 響度 → 寫 metadata。

        YouTube 與本機匯入唯一的差別在這一步**之前**（一個要下載，一個要轉檔），
        所以這裡是兩條路合流的地方 —— 抽出來也是為了讓「匯入的歌跟點播的歌
        完全一樣」這件事在程式碼上看得出來，而不是靠兩份平行的複製貼上維持。
        """
        loop = asyncio.get_event_loop()
        inst_file = song_dir / "instrumental.mp3"
        voc_file = song_dir / "vocals.mp3"
        lyrics_file = song_dir / "lyrics.json"
        pitch_file = song_dir / "pitch.json"
        title = meta.get("title", "Unknown Title")
        artist = meta.get("artist", "")

        # 2. Vocal Separation
        if progress_callback:
            progress_callback(song_id, "AI Separating Instrumental & Vocals (Demucs)...", 45)
        logger.info(f"[{song_id}] Separating stems...")
        inst_path, voc_path = await loop.run_in_executor(
            None, self.separator.separate, audio_path, song_dir
        )

        # 3. Lyrics Alignment
        if progress_callback:
            progress_callback(song_id, "Fetching & Aligning Karaoke Lyrics (Word-Level)...", 75)
        logger.info(f"[{song_id}] Aligning lyrics...")
        await loop.run_in_executor(
            None, self.lyrics_aligner.align, voc_path, title, artist, lyrics_file, local_lrc
        )

        # 4. Pitch Extraction
        if progress_callback:
            progress_callback(song_id, "Extracting Pitch Curve & Guide Notes...", 90)
        logger.info(f"[{song_id}] Extracting pitch...")
        await loop.run_in_executor(
            None, self.pitch_extractor.extract_pitch, voc_path, pitch_file
        )

        # 5. 響度量測（自動音量平衡）
        # 量伴奏軌，因為那才是實際播放出來的主體。量不到就當作沒有這筆資料，
        # 播放端會退回不套用任何增益 —— 不能因為量測失敗就讓整首歌處理失敗。
        if progress_callback:
            progress_callback(song_id, "Measuring Loudness (EBU R128)...", 96)
        loudness = await loop.run_in_executor(
            None, analyze_audio_file, inst_path, self.loudness_target_lufs
        )
        if loudness:
            meta["loudness"] = loudness
            logger.info(f"[{song_id}] 響度 {loudness['lufs']} LUFS，建議增益 {loudness['gain_db']} dB")

        # Update Metadata
        meta["instrumental_path"] = str(inst_file)
        meta["vocals_path"] = str(voc_file)
        meta["lyrics_path"] = str(lyrics_file)
        meta["pitch_path"] = str(pitch_file)
        meta["status"] = "READY"

        import json
        with open(song_dir / "metadata.json", 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        if progress_callback:
            progress_callback(song_id, "Ready to Sing!", 100)
        logger.info(f"[{song_id}] Processing pipeline completed successfully.")
        return meta

    async def process_local_song(
        self,
        song_id: str,
        progress_callback: Optional[Callable[[str, str, int], None]] = None
    ) -> Dict[str, Any]:
        """
        本機匯入的那條路：把 `cache/import/` 裡的一個檔案變成曲庫裡的一首歌。

        跟 YouTube 那條路的差別只有「原料從哪來」：這裡用 ffmpeg 抽音訊、
        搬影像、抽一張縮圖，抽完之後交給 `_finish_song` —— 從那裡開始
        兩者完全相同。

        **影像失敗不算失敗**（見 local_media.py 第 1 點）：沒有 MV 的歌照樣
        唱得了，舞台會用情境背景。但**音訊失敗一定算失敗** —— 沒有聲音就
        沒有歌，而這時候要讓使用者知道是哪一個檔案有問題。
        """
        if self.local_library is None:
            raise RuntimeError("這台機器沒有啟用本機匯入")

        entry = self.local_library.entry(song_id)
        if not entry:
            raise FileNotFoundError(f"找不到匯入紀錄：{song_id}")

        song_dir = SONGS_DIR / song_id
        meta_file = song_dir / "metadata.json"
        if self.is_song_ready(song_id):
            logger.info(f"本機歌曲 {song_id} 已經處理過了")
            if progress_callback:
                progress_callback(song_id, "Ready (Cached)", 100)
            import json
            with open(meta_file, 'r', encoding='utf-8') as f:
                return json.load(f)

        loop = asyncio.get_event_loop()
        try:
            # 來源檔案不在就立刻停 —— 這是本機匯入最常見的失敗（隨身碟被拔掉、
            # 檔案被搬走），而它的錯誤訊息要講得出「放回哪裡」。
            src = self.local_library.source_path(song_id)
            song_dir.mkdir(parents=True, exist_ok=True)

            if progress_callback:
                progress_callback(song_id, f"讀取本機檔案：{src.name}", 8)
            info = await loop.run_in_executor(None, local_media.probe_media, src)
            if info is None:
                raise RuntimeError(f"讀不懂這個檔案（ffmpeg 打不開）：{entry.get('path', '')}")
            if not info.get("has_audio"):
                raise RuntimeError(f"這個檔案裡沒有音訊軌：{entry.get('path', '')}")

            # 歌名與歌手的優先序：使用者在畫面上打的 > 檔案內嵌標籤 > 檔名解析。
            # 內嵌標籤排在檔名前面是因為從 CD 抓軌／音樂庫匯出的檔案，
            # 那兩個欄位是正確的中繼資料，而檔名可能已經被整理軟體改成編號。
            title = str(entry.get("title") or "").strip()
            artist = str(entry.get("artist") or "").strip()
            if not entry.get("title_from_user"):
                title = str(info.get("title") or "").strip() or title
                artist = str(info.get("artist") or "").strip() or artist
            title = title or src.stem

            if progress_callback:
                progress_callback(song_id, "抽出音訊…", 18)
            audio_path = song_dir / "original_audio.mp3"
            ok = await loop.run_in_executor(None, local_media.extract_audio, src, audio_path)
            if not ok:
                raise RuntimeError(f"抽不出音訊（檔案可能損壞）：{entry.get('path', '')}")

            video_path = song_dir / "original_video.mp4"
            has_video = False
            if info.get("has_video"):
                if progress_callback:
                    progress_callback(song_id, "準備背景影片…", 30)
                has_video = await loop.run_in_executor(
                    None, local_media.prepare_video, src, video_path)
                if not has_video:
                    logger.info(f"[{song_id}] 影像處理不了，這首歌改用情境背景")

            thumbnail = ""
            if has_video:
                thumb_path = song_dir / "thumbnail.jpg"
                got = await loop.run_in_executor(
                    None, local_media.grab_thumbnail, src, thumb_path,
                    float(info.get("duration") or 0))
                if got:
                    # 相對網址：`/media/songs` 是 SONGS_DIR 的靜態掛載點，
                    # 寫成絕對網址的話，換一個網域或走反向代理就全部壞掉。
                    thumbnail = f"/media/songs/{song_id}/thumbnail.jpg"

            meta: Dict[str, Any] = {
                "id": song_id,
                "title": title[:120],
                "artist": artist[:60],
                "duration": int(float(info.get("duration") or 0)),
                "thumbnail": thumbnail,
                "url": "",
                # 這兩個欄位是「這首歌是匯入來的」的唯一紀錄。快取管理、
                # 重新處理、之後的匯入掃描都靠它們認人。
                "source": "local",
                "source_file": entry.get("path", ""),
                "video_path": str(video_path) if has_video else None,
                "audio_path": str(audio_path),
            }

            # 檔案旁邊那一份 .lrc 是這條路上最重要的歌詞來源（見 local_import.py）
            lrc_file = sidecar_lrc(src)
            local_lrc = None
            if lrc_file is not None:
                try:
                    local_lrc = lrc_file.read_text(encoding="utf-8", errors="ignore")
                    logger.info(f"[{song_id}] 使用檔案旁的歌詞 {lrc_file.name}")
                except OSError as e:
                    logger.warning(f"[{song_id}] 讀不到 {lrc_file.name}: {e}")

            meta = await self._finish_song(song_id, song_dir, audio_path, meta,
                                           progress_callback=progress_callback,
                                           local_lrc=local_lrc)
            self.local_library.mark(song_id, "done")
            return meta

        except Exception as e:
            logger.exception(f"本機匯入失敗 {song_id}: {e}")
            self.local_library.mark(song_id, "error", str(e))
            if progress_callback:
                progress_callback(song_id, f"Error: {str(e)}", -1)
            raise
