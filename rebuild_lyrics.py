"""
重算已快取歌曲的歌詞時間軸。

改過對齊邏輯之後，cache 裡的舊 lyrics.json 仍是舊演算法的產物，
必須重跑才會套用新的 LRC 仿射校正與能量逐字分配。

用法：
    python rebuild_lyrics.py                # 重算全部快取歌曲
    python rebuild_lyrics.py bu7nU9Mhpyo    # 只重算指定 song_id
    python rebuild_lyrics.py --check        # 只檢查現況，不動檔案
"""
import sys
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.config import SONGS_DIR
from backend.pipeline.lyrics_aligner import LyricsAligner
from backend.config import WHISPER_MODEL_SIZE, DEVICE, COMPUTE_TYPE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("rebuild")


def describe(lyrics):
    """回報一份歌詞時間軸的健康度指標。"""
    if not lyrics:
        return "0 行"
    overlaps = sum(1 for a, b in zip(lyrics, lyrics[1:], strict=False) if b["start"] < a["end"] - 0.01)
    backwards = sum(1 for a, b in zip(lyrics, lyrics[1:], strict=False) if b["start"] < a["start"])
    longest = max(ln["end"] - ln["start"] for ln in lyrics)
    return (f"{len(lyrics)} 行 | 重疊 {overlaps} | 逆序 {backwards} | "
            f"最長行 {longest:.1f}s | {lyrics[0]['start']:.2f}s ~ {lyrics[-1]['end']:.2f}s")


def main():
    args = [a for a in sys.argv[1:]]
    check_only = "--check" in args
    targets = [a for a in args if not a.startswith("--")]

    dirs = sorted(d for d in SONGS_DIR.iterdir() if d.is_dir())
    if targets:
        dirs = [d for d in dirs if d.name in targets]

    aligner = LyricsAligner(model_size=WHISPER_MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
    done, skipped = 0, 0

    for song_dir in dirs:
        meta_file = song_dir / "metadata.json"
        voc_file = song_dir / "vocals.mp3"
        lyrics_file = song_dir / "lyrics.json"

        if not meta_file.exists() or not voc_file.exists():
            log.warning(f"[{song_dir.name}] 缺 metadata 或 vocals.mp3，跳過")
            skipped += 1
            continue

        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        title = meta.get("title", "")
        artist = meta.get("artist", "")

        print("\n" + "=" * 78)
        print(f"[{song_dir.name}] {title}")

        if lyrics_file.exists():
            try:
                print(f"  舊：{describe(json.loads(lyrics_file.read_text(encoding='utf-8')))}")
            except Exception:
                pass

        if check_only:
            continue

        try:
            lyrics = aligner.align(voc_file, title, artist, lyrics_file)
            report = json.loads((song_dir / "alignment.json").read_text(encoding="utf-8"))
            print(f"  新：{describe(lyrics)}")
            print(f"  對齊：來源={report['source']} scale={report['scale']} "
                  f"offset={report['offset']:+.3f}s score={report['score']}")
            done += 1
        except Exception as e:
            log.exception(f"[{song_dir.name}] 重算失敗: {e}")
            skipped += 1

    print("\n" + "=" * 78)
    print(f"完成 {done} 首，跳過 {skipped} 首。")


if __name__ == "__main__":
    main()
