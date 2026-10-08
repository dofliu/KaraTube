"""
歌詞取得與時間軸對齊

策略：LRC 主導、聲學校正。

網路上的 LRC（Netease / LRCLIB）本身就是人工校過的高品質時間軸，
會不準只是因為 YouTube 上的版本與原始錄音之間存在
  (a) 固定偏移 —— 片頭被裁掉或多了一段開場，
  (b) 線性變速 —— 上傳者為了規避 Content ID 把整首調快 1~5%。
所以正確做法是估計 (scale, offset) 這個仿射變換，
而不是丟掉 LRC 時間、改用 Whisper 轉錄結果重新模糊比對 ——
後者會把一份 47 行的正確歌詞打成 30 行、還帶重疊與亂序。
"""
import re
import json
import logging
import os
import requests
import urllib.parse
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from backend.pipeline.vocal_activity import VocalActivity

logger = logging.getLogger("KaraTube.LyricsAligner")

# --- 對齊參數 ---
# 支援 ±8% 變速上傳。原本只到 ±5%，而 nightcore 風格或為了避開比對調快 6~7% 的
# 上傳並不少見：超出網格時搜尋會收斂到一個「看起來分數還行」的錯誤 offset，
# 端對端評估量到整首字幕歪 3~6 秒、而且越唱越歪 —— 正是「越唱越歪」那一類症狀。
SCALE_GRID = np.arange(0.92, 1.08001, 0.0025)
OFFSET_RANGE = 25.0                              # 片頭最多裁切/新增 25 秒
# 連唱進來的句首（前面沒有靜音）改吸附到這個半徑內、強度至少這麼多的換字證據
START_FLUX_WINDOW = 0.30
START_FLUX_MIN = 0.5
# 分段平移（見 _piecewise_offsets）：MV 版／剪輯版跟 LRC 的錄音版本中段長度不同
PIECE_RANGE = 20.0                               # 一段最多相對全域結果再平移幾秒
PIECE_STEP = 0.05
PIECE_QUIET = 1.00                               # 換段處新位置前至少這麼久沒人唱
PIECE_ENTRY_FIT = 0.80                           # 新段落第一行的起唱分數下限（見 _line_fit）
PIECE_JITTER = 0.25                              # 每一行自己的打點誤差容許量
PIECE_SWITCH_COST = 1.2                          # 換段懲罰：約要連續三、四行的證據
SNAP_WINDOW = 0.70                               # 行首吸附真實起唱點的搜尋半徑
# 一句歌詞之內，連續靜音要超過這麼久才算「這句唱完了」。
# 0.35 秒會把句中換氣（流行歌常見 0.4~0.8 秒）當成句尾，後半句的字被擠進
# 前半句的時間裡 —— 端對端評估量到的是整句走字提早 2~3 秒唱完。
LINE_BREATH_GAP = 0.90
# 句尾那個字通常拖長音（樂句末延長）。逐字分配的先驗權重乘上這個倍數；
# 真正的長度仍以人聲軌上找得到的換字點為準，這只是找不到時的預設。
FINAL_HOLD_WEIGHT = 2.2
# 逐字切分的兩個權重（見 _segment_chars）：字長偏離先驗的懲罰、換字點落在聲學證據上的加分
SEG_LENGTH = 1.0
SEG_EVIDENCE = 2.0
# 對齊演算法的版本，寫進每一首的 alignment.json。
# 改過演算法之後，快取裡的舊時間軸不會自己更新 —— 有了這個欄位，
# `python rebuild_lyrics.py --stale` 才挑得出「還是舊版算的」那幾首。
#   1（或欄位不存在）：v1.36 以前，逐字按能量均分、句中換氣即句尾、變速 ±5%
#   2：v1.37，逐字切分（頻譜換字點 + 字長先驗）、換氣不斷句、變速 ±8%
#   3：v1.38，分段平移（MV 版／剪輯版中段長度跟 LRC 不同時，後半段不再整段歪掉）
ALIGNER_VERSION = 3
MIN_TRUST_SCORE = 0.28                           # 低於此分數視為抓到別首歌的 LRC

# LRC 檔頭的製作名單／版權聲明。只認「欄位名 + 冒號」這種結構，
# 不做全文關鍵字比對 —— 否則「一整瓶的夢境」這種含「曲」「詞」的正常歌詞會被誤殺。
METADATA_LINE_PATTERN = re.compile(
    r'^\s*[\(\[【]?\s*('
    r'作\s*詞|作\s*词|作\s*曲|編\s*曲|编\s*曲|製\s*作|制\s*作|監\s*製|监\s*制|'
    r'混\s*音|錄\s*音|录\s*音|母\s*帶|和\s*聲|和\s*声|配\s*唱|企\s*劃|企\s*划|'
    r'出\s*品|發\s*行|发\s*行|製作人|制作人|演\s*唱|原\s*唱|歌\s*手|吉\s*他|貝\s*斯|贝\s*斯|'
    r'鼓|弦\s*樂|弦\s*乐|鍵\s*盤|键\s*盘|詞|词|曲|唱|'
    r'OP|SP|ISRC|Lyricist|Composer|Arranger|Producer|Mixing|Mastering|Vocals?|Guitar|Drums|Bass'
    r')\s*[:：/／∶]',
    re.IGNORECASE
)
COPYRIGHT_PATTERN = re.compile(
    r'(版權所有|版权所有|未經(授權|許可)|未经(授权|许可)|禁止(轉載|翻唱|商用)|'
    r'All\s+rights\s+reserved|Copyright|℗|©|本歌詞由.*提供|歌詞來源)',
    re.IGNORECASE
)
# 舊版沿用的名稱，僅保留給純聲學轉錄的雜訊過濾使用
DISCLAIMER_PATTERN = METADATA_LINE_PATTERN


def _write_json_atomic(path: Path, payload: Any) -> None:
    """先寫 .tmp 再 os.replace：中途失敗不會留下半份 JSON 給下一個讀的人。"""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class LyricsAligner:
    def __init__(self, model_size: str = "base", device: str = "cuda", compute_type: str = "float16", **kwargs):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._whisper_model = None

    # ------------------------------------------------------------------
    # 文字工具
    # ------------------------------------------------------------------
    def _to_traditional_chinese(self, text: str) -> str:
        try:
            import zhconv
            return zhconv.convert(text, 'zh-tw')
        except Exception:
            return text

    def _clean_cjk(self, text: str) -> str:
        try:
            import zhconv
            return re.sub(r'[^一-鿿]', '', zhconv.convert(text, 'zh-cn'))
        except Exception:
            return re.sub(r'[^\w]', '', text)

    def _get_whisper_model(self):
        if self._whisper_model is None:
            import whisper
            logger.info(f"Loading Whisper model '{self.model_size}' on {self.device}...")
            self._whisper_model = whisper.load_model(self.model_size, device=self.device)
        return self._whisper_model

    def _title_matches(self, query_title: str, result_name: str) -> bool:
        try:
            import zhconv
            q = re.sub(r'[^\w]', '', zhconv.convert(query_title, 'zh-cn')).lower()
            r = re.sub(r'[^\w]', '', zhconv.convert(result_name, 'zh-cn')).lower()
            if not q or not r:
                return False
            q_cjk = re.findall(r'[一-鿿]', q)
            r_cjk = re.findall(r'[一-鿿]', r)
            if len(q_cjk) >= 2:
                if not r_cjk:
                    return False
                cjk_overlap = len(set(q_cjk) & set(r_cjk)) / min(len(set(q_cjk)), len(set(r_cjk)))
                return cjk_overlap >= 0.6
            if q in r or r in q:
                return True
            overlap = len(set(q) & set(r)) / max(len(q), len(r))
            return overlap >= 0.4
        except Exception:
            return True

    def _rank_candidate(self, name: str, artists: str, duration_sec: float,
                        artist_hint: str, duration_hint: float) -> float:
        """
        替搜尋結果打分。曲長是最強的訊號 —— 翻唱、Live、加長版的長度一定對不上，
        而它們的時間軸差異是全域仿射校正救不回來的。
        """
        score = 0.0
        low = f"{name} {artists}".lower()

        if duration_hint > 0 and duration_sec > 0:
            diff = abs(duration_sec - duration_hint)
            if diff <= 3:
                score += 3.0
            elif diff <= 8:
                score += 1.5
            elif diff <= 20:
                score += 0.2
            else:
                score -= 2.5

        if artist_hint:
            a_hint = re.sub(r'[^\w]', '', artist_hint).lower()
            if a_hint and artists and self._title_matches(artist_hint, artists):
                score += 2.0

        for bad, penalty in (('伴奏', 4.0), ('純音樂', 4.0), ('纯音乐', 4.0), ('instrumental', 4.0),
                             ('karaoke', 3.0), ('cover', 2.0), ('翻唱', 2.0), ('remix', 2.5),
                             ('montagem', 3.0), ('nightcore', 3.0), ('sped up', 3.0),
                             ('slowed', 3.0), ('mashup', 2.5), ('medley', 2.5),
                             ('串燒', 2.5), ('串烧', 2.5), ('dj', 1.5), ('live', 1.5),
                             ('演唱會', 1.5), ('演唱会', 1.5), ('伴唱', 2.0), ('acoustic', 1.0)):
            if bad in low:
                score -= penalty
        return score

    def _lrc_usable(self, lrc_text: str, duration_hint: float = 0.0) -> bool:
        """
        真正解析過才算數。搜尋結果標題對得上不代表歌詞有時間軸 ——
        混音版、純伴奏頁面常常只回一段沒有時間標籤的說明文字。
        """
        if not lrc_text or len(lrc_text) < 100:
            return False
        parsed = self.parse_lrc_with_timestamps(lrc_text)
        if len(parsed) < 4:
            return False
        if duration_hint > 0 and parsed[-1]['time'] > duration_hint + 45:
            return False
        return True

    def _is_non_singing(self, text: str) -> bool:
        """判斷是否為製作名單／版權聲明，而不是要唱的歌詞。"""
        text = text.strip()
        if not text:
            return True
        if METADATA_LINE_PATTERN.match(text):
            return True
        if COPYRIGHT_PATTERN.search(text):
            return True
        # 整行被【】或[]包起來的，慣例上都是註記
        if re.fullmatch(r'[\(\[【《].*[\)\]】》]', text):
            return True
        return False

    # ------------------------------------------------------------------
    # LRC 取得與解析
    # ------------------------------------------------------------------
    def fetch_synced_lrc(self, track_name: str, artist_name: str = "",
                         duration_hint: float = 0.0) -> Optional[str]:
        """向下相容的單一結果版本。"""
        found = self.fetch_lrc_candidates(track_name, artist_name, duration_hint, max_candidates=1)
        return found[0]['lrc'] if found else None

    def fetch_lrc_candidates(self, track_name: str, artist_name: str = "",
                             duration_hint: float = 0.0,
                             max_candidates: int = 3) -> List[Dict[str, Any]]:
        candidates = []

        for bm in re.finditer(r'[【《\[「](.*?)[】》\]」]', track_name):
            cand = bm.group(1).strip()
            c_cjk = ''.join(re.findall(r'[一-鿿]+', cand))
            if len(c_cjk) >= 2:
                candidates.append(c_cjk)
            candidates.append(cand)

        title_cjk = ''.join(re.findall(r'[一-鿿]+', track_name))
        if len(title_cjk) >= 2 and title_cjk not in candidates:
            candidates.append(title_cjk)

        dash_prefix = re.split(r'\s*-\s*', track_name)[0].strip()
        if dash_prefix and dash_prefix not in candidates:
            candidates.append(dash_prefix)

        clean = re.sub(r'[\(\[\{【《「].*?[\)\]\}】》」]', '', track_name).strip()
        clean = re.sub(r'(Official\s*Music\s*Video|Official\s*MV|MV|HD|1080P|4K|主題曲|原聲帶|完整版|高音質|Lyric\s*Video|Topic)', '', clean, flags=re.IGNORECASE).strip()
        if clean and clean not in candidates:
            candidates.append(clean)

        if track_name not in candidates:
            candidates.append(track_name)

        expanded = []
        for c in candidates:
            expanded.append(c)
            if artist_name and artist_name not in c:
                clean_ar = re.sub(r'[\(\[\{【《「].*?[\)\]\}】》」]', '', artist_name).strip()
                expanded.append(f"{clean_ar} {c}")
                expanded.append(f"{c} {clean_ar}")

        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        noise_re = re.compile(r'(Official\s*Music\s*Video|Official\s*MV|MV|HD|1080P|4K|主題曲|原聲帶|完整版|高音質|Lyric\s*Video|Topic)', re.IGNORECASE)

        # 先把所有查詢字串的搜尋結果彙整成一個候選池，全域排序後才去抓歌詞。
        # 舊做法是「第一個標題對得上就用」，很容易挑到翻唱或混音版。
        pool: List[Tuple[float, str, Any, str, str, float]] = []
        seen = set()
        queries_done = 0

        for q in expanded:
            if not q or len(q) < 2:
                continue
            if queries_done >= 4 or len(pool) >= 12:
                break

            clean_q = noise_re.sub('', q).strip()
            if not clean_q or clean_q in seen:
                continue
            seen.add(clean_q)
            hit = False

            try:
                url = f"https://music.163.com/api/cloudsearch/pc?s={urllib.parse.quote(clean_q)}&type=1&offset=0&limit=10"
                r = requests.get(url, headers=headers, timeout=4).json()
                for s in r.get('result', {}).get('songs', []):
                    name = s.get('name', '')
                    if not any(self._title_matches(c, name) for c in candidates):
                        continue
                    key = ('netease', s['id'])
                    if key in seen:
                        continue
                    seen.add(key)
                    artists = " ".join(a.get('name', '') for a in s.get('ar', []) or [])
                    dur = float(s.get('dt', 0)) / 1000.0
                    pool.append((self._rank_candidate(name, artists, dur, artist_name, duration_hint),
                                 'netease', s['id'], name, artists, dur))
                    hit = True
            except Exception:
                pass

            try:
                lrclib_url = f"https://lrclib.net/api/search?q={urllib.parse.quote(clean_q)}"
                r = requests.get(lrclib_url, timeout=4).json()
                if isinstance(r, list):
                    for item in r:
                        track = item.get("trackName", "") or ""
                        synced = item.get("syncedLyrics")
                        if not synced or not any(self._title_matches(c, track) for c in candidates):
                            continue
                        key = ('lrclib', item.get('id'))
                        if key in seen:
                            continue
                        seen.add(key)
                        artists = item.get("artistName", "") or ""
                        dur = float(item.get("duration") or 0)
                        pool.append((self._rank_candidate(track, artists, dur, artist_name, duration_hint),
                                     'lrclib', synced, track, artists, dur))
                        hit = True
            except Exception:
                pass

            if hit:
                queries_done += 1

        pool.sort(key=lambda x: -x[0])

        found: List[Dict[str, Any]] = []
        for rank, provider, payload, name, artists, dur in pool[:8]:
            if len(found) >= max_candidates:
                break
            lrc = None
            if provider == 'lrclib':
                lrc = payload
            else:
                try:
                    lrc_r = requests.get(
                        f"https://music.163.com/api/song/lyric?os=pc&id={payload}&lv=-1&kv=-1&tv=-1",
                        headers=headers, timeout=4).json()
                    lrc = lrc_r.get('lrc', {}).get('lyric', '')
                except Exception:
                    continue
            if not self._lrc_usable(lrc, duration_hint):
                continue
            # 同一份歌詞可能在兩個站都有，用行數+末行時間粗略去重
            parsed = self.parse_lrc_with_timestamps(lrc)
            sig = (len(parsed), round(parsed[-1]['time']))
            if any(f['sig'] == sig for f in found):
                continue
            found.append({'lrc': lrc, 'name': name, 'artists': artists,
                          'duration': dur, 'rank': rank, 'sig': sig,
                          'provider': provider, 'parsed': parsed})

        for f in found:
            logger.info(f"LRC 候選: [{f['provider']}] {f['name']} / {f['artists']} "
                        f"({f['duration']:.0f}s, {len(f['parsed'])} 行, 匹配分 {f['rank']:+.1f})")
        return found

    def extract_lyric_lines_from_lrc(self, lrc_text: str) -> List[str]:
        return [item['text'] for item in self.parse_lrc_with_timestamps(lrc_text)]

    def parse_lrc_with_timestamps(self, lrc_text: str) -> List[Dict[str, Any]]:
        """解析 LRC。保留每一行與原順序，不做任何丟棄式過濾。"""
        lines = []
        # 小數位接受 1~3 位：標準寫法是百分秒（[00:12.50]），
        # 但實際流通的 LRC 有寫成 [00:12.5] 的，整行丟掉會少一句歌詞。
        time_tag_pattern = re.compile(r'\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]')
        for raw_line in lrc_text.splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            tags = time_tag_pattern.findall(raw_line)
            if not tags:
                continue
            text = time_tag_pattern.sub('', raw_line).strip()
            if not text or self._is_non_singing(text):
                continue

            text_tw = self._to_traditional_chinese(text)
            cjk = self._clean_cjk(text)
            for tag in tags:
                minutes = int(tag[0])
                seconds = int(tag[1])
                millis = int(tag[2].ljust(3, '0')[:3]) if tag[2] else 0
                total_sec = round(minutes * 60 + seconds + millis / 1000.0, 3)
                lines.append({'time': total_sec, 'text': text_tw, 'cjk': cjk})

        lines.sort(key=lambda x: x['time'])

        # 同一時間戳重複出現（雙語 LRC 常見）只留第一筆
        deduped = []
        for item in lines:
            if deduped and abs(item['time'] - deduped[-1]['time']) < 0.02:
                continue
            deduped.append(item)
        return deduped

    # ------------------------------------------------------------------
    # 時間軸對齊
    # ------------------------------------------------------------------
    @staticmethod
    def _line_fit(T: np.ndarray, cover_len: np.ndarray, va: VocalActivity) -> np.ndarray:
        """
        每一行放在 T 這個起點時「像不像起唱」：0.55 × onset + 0.45 × cover。

          onset —— 行首之後有聲、之前無聲（真正的「起唱」特徵）
          cover —— 整行預期演唱區間確實有人聲

        T 的最後一維是行；cover_len 是每行的預期長度（格數），可廣播到 T。
        """
        fs, N = va.frame_sec, va.n_frames
        cum = va.cum_active

        def win_mean(start_sec: np.ndarray, length_frames) -> np.ndarray:
            a = np.clip(np.round(start_sec / fs).astype(np.int64), 0, N)
            b = np.clip(a + np.asarray(length_frames, dtype=np.int64), 0, N)
            return (cum[b] - cum[a]) / np.maximum(b - a, 1)

        post = win_mean(T, int(0.40 / fs))
        pre = win_mean(T - 0.55, int(0.45 / fs))
        onset = np.clip(post - pre, -1.0, 1.0)
        cover = win_mean(T, cover_len)
        return 0.55 * onset + 0.45 * cover

    def _estimate_time_warp(self, times: np.ndarray, durations: np.ndarray,
                            va: VocalActivity) -> Tuple[float, float, float]:
        """
        在 (scale, offset) 空間網格搜尋，讓 LRC 行首盡量落在真實的人聲起點上
        （每一行的分數見 `_line_fit`，這裡取全首平均）。
        """
        fs, N = va.frame_sec, va.n_frames
        if N == 0 or times.size < 3:
            return 1.0, 0.0, 0.0

        def score_for(scale: float, offsets: np.ndarray) -> np.ndarray:
            T = scale * times[None, :] + offsets[:, None]          # (O, L)
            cover_len = np.maximum(np.round(scale * durations / fs), 1).astype(np.int64)
            s = self._line_fit(T, cover_len[None, :], va).mean(axis=1)

            in_range = ((T >= -0.5) & (T <= va.duration)).mean(axis=1)
            s -= 0.60 * (1.0 - in_range)                            # 把歌詞推到音檔外面不算對齊
            s -= 0.12 * np.minimum(1.0, abs(scale - 1.0) / 0.05)    # 無證據時偏好不變速
            s -= 0.02 * np.minimum(1.0, np.abs(offsets) / OFFSET_RANGE)
            return s

        coarse_off = np.arange(-OFFSET_RANGE, OFFSET_RANGE + 1e-6, 0.05)
        best_scale, best_off, best_score = 1.0, 0.0, -9.9
        for sc in SCALE_GRID:
            s = score_for(float(sc), coarse_off)
            k = int(np.argmax(s))
            if s[k] > best_score:
                best_scale, best_off, best_score = float(sc), float(coarse_off[k]), float(s[k])

        # 精修：offset 到 5ms、scale 到 0.05%
        fine_off = np.arange(best_off - 0.10, best_off + 0.10001, 0.005)
        for sc in np.arange(best_scale - 0.0025, best_scale + 0.00251, 0.0005):
            s = score_for(float(sc), fine_off)
            k = int(np.argmax(s))
            if s[k] > best_score:
                best_scale, best_off, best_score = float(sc), float(fine_off[k]), float(s[k])

        logger.info(f"時間軸校正: scale={best_scale:.4f} offset={best_off:+.3f}s score={best_score:.3f}")
        return best_scale, best_off, best_score

    def _piecewise_offsets(self, times: np.ndarray, durations: np.ndarray, scale: float,
                           offset: float, va: VocalActivity) -> Tuple[np.ndarray, int]:
        """
        全域仿射之後，允許歌曲中段整段平移（回傳每一行的 offset、平移了幾次）。

        為什麼需要：LRC 對應的是錄音室版本，YouTube 上常常是 MV 版、現場版、
        剪輯版 —— 中段插了一段劇情、間奏剪短、多講一段話。整首不是同一個仿射變換，
        全域 (scale, offset) 只能對上其中一半；另一半整段差 7~9 秒
        （端對端評估「MV 中段多 7 秒」「間奏剪短 9 秒」兩個情境），
        唱的人看到的就是「唱到一半字幕突然整段跳開」。每行吸附起唱點的半徑只有
        0.7 秒，救不回來。

        做法：每一行可以在全域結果上再加一個 Δ（±PIECE_RANGE 秒），用 Viterbi 找
        「每行的起唱分數總和 − 換段懲罰」最大的 Δ 序列。
          * 換段要付 PIECE_SWITCH_COST，大約要連續三、四行的證據才值得換 ——
            單一行對到別的起唱點不會把整段帶走；
          * 時間不能倒退：下一行的起點不能早於上一行起點 + 0.3 秒；
          * 結果整段幾乎都是 Δ=0（沒有結構差異的歌）時，原樣回傳全域 offset。
        """
        L = times.size
        base = np.full(L, offset, dtype=np.float64)
        if L < 6 or va.n_frames == 0:
            return base, 0
        fs = va.frame_sec
        deltas = np.round(np.arange(-PIECE_RANGE, PIECE_RANGE + 1e-6, PIECE_STEP), 3)
        D = deltas.size
        T = scale * times[:, None] + offset + deltas[None, :]       # (L, D)
        cover_len = np.maximum(np.round(scale * durations / fs), 1).astype(np.int64)
        emit = self._line_fit(T, cover_len[:, None], va)
        emit[(T < -0.5) | (T > va.duration)] = -1.0
        # LRC 每一行本身就有 ±0.1~0.2 秒的打點誤差，單行的分數在正確 Δ 附近很尖：
        # 每行取 ±PIECE_JITTER 內最好的那一格（精確位置交給之後的行首吸附）
        r = int(round(PIECE_JITTER / PIECE_STEP))
        if r > 0:
            from numpy.lib.stride_tricks import sliding_window_view
            padded = np.pad(emit, ((0, 0), (r, r)), mode="edge")
            emit = sliding_window_view(padded, 2 * r + 1, axis=1).max(axis=-1)
        # 不換段時偏好 Δ=0：相同證據下不要無緣無故整段搬家
        emit -= 0.002 * np.abs(deltas)[None, :]
        # 只准在「新位置前面有一段人聲靜音」的行換段。真的版本差異（MV 插了劇情、
        # 間奏剪短）在人聲軌上一定是一段沒人唱的空檔；連唱的段落裡整段往後滑
        # 一行也對得上起唱點與覆蓋率，沒有這一條會被當成結構差異（量到過把副歌
        # 最後六行整段搬錯一行）。
        cum = va.cum_active
        qa = np.clip(np.round((T - PIECE_QUIET) / fs).astype(np.int64), 0, va.n_frames)
        qb = np.clip(np.round((T - 0.10) / fs).astype(np.int64), 0, va.n_frames)
        quiet = (cum[qb] - cum[qa]) <= 0.25 * np.maximum(qb - qa, 1)
        # 而且新段落的第一行必須是一個乾淨的起唱（靜音之後馬上開口）。只有靜音
        # 不夠：句中換氣後重新開口也長得像起唱，一段連唱的句子整段往前挪到
        # 換氣點上，覆蓋率照樣很高。
        entry = quiet & (emit >= PIECE_ENTRY_FIT)

        NEG = -1e18
        score = emit[0].copy()
        back = np.zeros((L, D), dtype=np.int64)
        idx = np.arange(D)
        for i in range(1, L):
            # 不換段（Δ 不變）：LRC 的行距本身至少要 0.3 秒
            slack = scale * (times[i] - times[i - 1]) - 0.30
            # 換段：可接受的 Δ_prev 是 deltas ≤ Δ + sw_slack；取其中分數最高者（前綴最大值）
            pm_val = np.maximum.accumulate(score)
            pm_arg = np.maximum.accumulate(np.where(score >= pm_val, idx, 0))
            # 換段時更嚴：上一行要在新位置前的那段靜音之前唱完（它自己的長度 + 靜音）。
            # 只要求不倒退的話，新段落會從「上一行的起唱點」進場 —— 整段錯一行。
            sw_slack = scale * (times[i] - times[i - 1]) - scale * durations[i - 1] - PIECE_QUIET
            lim = np.searchsorted(deltas, deltas + sw_slack + 1e-9, side="right") - 1
            switch_val = np.where(lim >= 0, pm_val[np.clip(lim, 0, D - 1)], NEG) - PIECE_SWITCH_COST
            switch_arg = pm_arg[np.clip(lim, 0, D - 1)]
            switch_val = np.where(entry[i], switch_val, NEG)
            stay_ok = slack >= 0
            stay_val = score if stay_ok else np.full(D, NEG)
            use_stay = stay_val >= switch_val
            back[i] = np.where(use_stay, idx, switch_arg)
            score = np.where(use_stay, stay_val, switch_val) + emit[i]

        j = int(np.argmax(score))
        path = np.zeros(L, dtype=np.int64)
        path[-1] = j
        for i in range(L - 1, 0, -1):
            j = int(back[i, j])
            path[i - 1] = j
        d = deltas[path]
        switches = int(np.count_nonzero(np.diff(d)))
        if not np.any(np.abs(d) > 0.75):
            return base, 0          # 只是幾十毫秒的游移，交給行首吸附處理
        # 每一段跟全域結果比，分數要真的變好才採用（防止在沒有人聲的區域亂搬）
        gain = float(emit[np.arange(L), path].sum() - emit[:, int(np.argmin(np.abs(deltas)))].sum())
        if gain < PIECE_SWITCH_COST * max(1, switches):
            return base, 0
        logger.info(f"分段平移: {switches} 處，Δ 範圍 {d.min():+.2f}~{d.max():+.2f}s，增益 {gain:.2f}")
        return base + d, switches

    def _refine_line_times(self, mapped: List[float], char_counts: List[int],
                           va: Optional[VocalActivity]) -> Tuple[List[float], List[float]]:
        """行首吸附真實起唱點，行尾切在人聲停止處。"""
        L = len(mapped)
        starts = [float(x) for x in mapped]

        if va is not None:
            for i in range(L):
                floor = starts[i - 1] + 0.30 if i > 0 else -0.5
                cand = va.nearest_onset(starts[i], window=SNAP_WINDOW)
                if cand is None and START_FLUX_WINDOW > 0:
                    # 上一句直接連唱進來（中間沒有靜音），起唱點偵測找不到它。
                    # 改找附近最強的換字證據 —— 句首差一個字，整句走字就錯一格。
                    near = [c for c in va.syllable_boundaries(starts[i] - START_FLUX_WINDOW,
                                                              starts[i] + START_FLUX_WINDOW)
                            if c[1] >= START_FLUX_MIN]
                    if near:
                        cand = min(near, key=lambda c: abs(c[0] - starts[i]))[0]
                # 找不到起唱點代表這行是連唱進來的，維持仿射映射結果即可
                if cand is not None and cand >= floor:
                    starts[i] = cand

        for i in range(1, L):
            if starts[i] < starts[i - 1] + 0.12:
                starts[i] = starts[i - 1] + 0.12
        starts = [max(0.0, s) for s in starts]

        total_dur = va.duration if va is not None else ((starts[-1] + 6.0) if starts else 0.0)
        ends = []
        for i in range(L):
            nxt = starts[i + 1] if i + 1 < L else total_dur
            hard = max(starts[i] + 0.25, nxt - 0.04)
            if va is not None:
                e = va.voice_end_after(starts[i], max_gap=LINE_BREATH_GAP, limit=hard,
                                      next_start=nxt if i + 1 < L else None)
            else:
                e = min(hard, starts[i] + char_counts[i] * 0.35)
            lo = starts[i] + max(0.40, char_counts[i] * 0.16)
            hi = min(hard, starts[i] + char_counts[i] * 0.95 + 1.0)
            e = min(max(e, lo), max(hi, starts[i] + 0.30))
            ends.append(round(e, 3))
        return [round(s, 3) for s in starts], ends

    @staticmethod
    def _char_weight(ch: str) -> float:
        """一個字元大概佔多少演唱時間。中文一字一音，拉丁字母只是音節的一部分。"""
        if ch.isspace():
            return 0.20
        if '一' <= ch <= '鿿' or '぀' <= ch <= 'ヿ' or '가' <= ch <= '힯':
            return 1.00
        if ch.isalnum():
            return 0.45
        return 0.30

    def _prior_weights(self, chars: List[str]) -> np.ndarray:
        """每個字預期佔多少演唱時間（相對值）。句尾最後一個字加上拖長音的倍數。"""
        w = np.array([self._char_weight(c) for c in chars], dtype=np.float64)
        if w.sum() <= 0:
            w = np.ones(len(chars))
        sung = [k for k, c in enumerate(chars) if not c.isspace()]
        if len(sung) >= 3:
            w[sung[-1]] *= FINAL_HOLD_WEIGHT
        return w

    @staticmethod
    def _segment_chars(weights: np.ndarray, start: float, end: float,
                       va: VocalActivity, cands: List[Tuple[float, float]]) -> Optional[List[float]]:
        """
        把一句切成 n 個字：在「每個字的長度合理」與「換字點落在聲學證據上」之間
        找最佳解（動態規劃）。回傳 n-1 個內部換字點，或 None（這一句沒有可信的人聲）。

          * 長度：每個字的**發聲時間**應該接近它的先驗份額（權重 ÷ 總權重 × 這句的
            總發聲時間）。偏離用對數平方計 —— 長一倍跟短一半一樣糟。
            用發聲時間不用牆上時間，句中換氣的那半秒才不會被算成某個字拖長音。
          * 證據：換字點落在 `syllable_boundaries` 找到的位置上，依強度加分。

        為什麼不是「每個預期點各自吸最近的候選」：連音（同一個音高、沒有子音）
        找不到換字點，這時候最近的候選是**下一個**字的；吸過去之後整句錯一格，
        前一個字長一倍、最後一個字只剩一眨眼 —— 端對端評估裡這正是剩下那三成
        大誤差的來源。長度項讓「錯一格」付出它真正的代價。
        """
        n = len(weights)
        if n < 2:
            return []
        fs = va.frame_sec
        a = int(np.clip(round(start / fs), 0, va.n_frames))
        b = int(np.clip(round(end / fs), 0, va.n_frames))
        if b - a < 4:
            return None
        voiced_total = float(va.cum_active[b] - va.cum_active[a]) * fs
        if voiced_total < 0.30 * (end - start):
            return None

        # 可以放換字點的位置：每 20ms 一格（無證據），加上所有候選點（有證據）
        grid = np.arange(start + 0.06, end - 0.06 + 1e-9, 0.02)
        pos = [(float(t), 0.0) for t in grid] + [
            (float(t), float(sv)) for t, sv in cands if start + 0.06 < t < end - 0.06]
        if not pos:
            return None
        pos.sort()
        P = np.array([t for t, _ in pos])
        reward = SEG_EVIDENCE * np.array([sv for _, sv in pos])

        def voiced_at(t: np.ndarray) -> np.ndarray:
            f = np.clip(np.round(np.asarray(t) / fs).astype(np.int64), 0, va.n_frames)
            return va.cum_active[f] * fs

        # 字的「長度」：發聲時間為主，加一點牆上時間避免整段靜音時長度變 0
        V = voiced_at(P)
        Vs, Ve = float(voiced_at(np.array([start]))[0]), float(voiced_at(np.array([end]))[0])

        def length(v0, v1, t0, t1):
            return np.maximum(v1 - v0 + 0.15 * (t1 - t0), 0.03)

        share = weights / weights.sum() * (voiced_total + 0.15 * (end - start))
        NEG = -1e18

        # 第 1 個換字點（字 0 從句首開始）
        dur0 = length(Vs, V, start, P)
        score = -SEG_LENGTH * np.log(dur0 / share[0]) ** 2 + reward
        score[P - start < 0.06] = NEG
        backs = []
        dV = V[None, :] - V[:, None]
        dT = P[None, :] - P[:, None]
        ok = dT >= 0.06
        for k in range(1, n - 1):
            dur = np.maximum(dV + 0.15 * dT, 0.03)
            cost = -SEG_LENGTH * np.log(dur / share[k]) ** 2
            total = np.where(ok, score[:, None] + cost, NEG)
            arg = np.argmax(total, axis=0)
            score = total[arg, np.arange(P.size)] + reward
            backs.append(arg)
        last = length(V, Ve, P, end)
        final = score - SEG_LENGTH * np.log(last / share[n - 1]) ** 2
        final[end - P < 0.06] = NEG
        j = int(np.argmax(final))
        if final[j] <= NEG / 2:
            return None
        idx = [j]
        for arg in reversed(backs):
            j = int(arg[j])
            idx.append(j)
        idx.reverse()
        return [float(P[i]) for i in idx]

    def _distribute_chars(self, text: str, start: float, end: float,
                          va: Optional[VocalActivity]) -> List[Dict[str, Any]]:
        """
        行內逐字時間 —— 走字跟不跟得上聲音，看的是這一段。

        1. 先驗：每個字的權重（中文一字一音、句尾拖長音）。
        2. 證據：人聲軌上像換字的位置（頻譜突變、換氣後開口），見
           `VocalActivity.syllable_boundaries`。
        3. 兩者在 `_segment_chars` 裡一起解：字長合理、換字點盡量落在證據上。
           沒有可信人聲時退回按權重的線性分配。

        舊做法（能量累積均分）等於假設每個字一樣長，句尾長音被壓成一般長度，
        句中的字一路跑在聲音前面半秒到一秒（端對端評估：中位誤差 370ms，
        過半的字差超過 300ms）。
        """
        chars = list(text)
        n = len(chars)
        if n == 0:
            return []
        span = max(0.05, end - start)

        w = self._prior_weights(chars)
        frac = (np.cumsum(w) / w.sum())[:-1]                      # n-1 個內部邊界

        linear = [start + span * f for f in frac]
        bounds = linear
        if va is not None and frac.size:
            cands = va.syllable_boundaries(start, end)
            seg = self._segment_chars(w, start, end, va, cands)
            if seg is not None and len(seg) == frac.size:
                bounds = seg

        pts = [start] + list(bounds) + [end]
        min_dur = min(0.05, span / n)
        for k in range(1, len(pts)):
            if pts[k] < pts[k - 1] + min_dur:
                pts[k] = pts[k - 1] + min_dur
        if pts[-1] > end + 1e-6:                                   # 極端狀況退回線性
            pts = [start + span * k / n for k in range(n + 1)]

        return [{'char': chars[k],
                 'start': round(pts[k], 3),
                 'end': round(pts[k + 1], 3)} for k in range(n)]

    def align_lrc_to_audio(self, parsed_lrc: List[Dict[str, Any]],
                           va: Optional[VocalActivity]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """把已解析的 LRC 對到這支影片的音訊上。輸出保留 LRC 的每一行與原順序。"""
        if not parsed_lrc:
            return [], {"source": "lrc", "scale": 1.0, "offset": 0.0, "score": 0.0, "lines": 0}

        times = np.array([x['time'] for x in parsed_lrc], dtype=np.float64)
        char_counts = [max(1, len(x['text'])) for x in parsed_lrc]

        # 每行的預期演唱長度：受限於到下一行的間隔
        gaps = np.diff(times, append=times[-1] + 4.0)
        est = np.clip(np.array(char_counts, dtype=np.float64) * 0.30, 0.8, 5.0)
        durations = np.minimum(est, np.maximum(gaps - 0.10, 0.5))

        if va is not None:
            scale, offset, score = self._estimate_time_warp(times, durations, va)
        else:
            scale, offset, score = 1.0, 0.0, 0.0

        shifts = 0
        score_shifted = None
        if va is not None:
            offsets, shifts = self._piecewise_offsets(times, durations, scale, offset, va)
            mapped = (scale * times + offsets).tolist()
            if shifts:
                # 平移後的分數只當診斷，不拿來決定信不信這份 LRC：分段平移對抓錯的歌
                # 也會硬湊出幾段（端對端評估量到 +0.03~0.10），拿它來過信任門檻
                # 等於多開一條讓別首歌的歌詞混過去的路。
                cl = np.maximum(np.round(scale * durations / va.frame_sec), 1).astype(np.int64)
                f0 = self._line_fit((scale * times + offset)[None, :], cl[None, :], va).mean()
                f1 = self._line_fit((scale * times + offsets)[None, :], cl[None, :], va).mean()
                score_shifted = score + float(f1 - f0)
        else:
            mapped = (scale * times + offset).tolist()
        starts, ends = self._refine_line_times(mapped, char_counts, va)

        lyrics = []
        for i, item in enumerate(parsed_lrc):
            lyrics.append({
                'line_idx': i,
                'start': starts[i],
                'end': ends[i],
                'text': item['text'],
                'words': self._distribute_chars(item['text'], starts[i], ends[i], va)
            })

        lyrics = self._split_long_lines(lyrics, va)

        report = {"source": "lrc", "scale": round(scale, 4), "offset": round(offset, 3),
                  "score": round(score, 3), "lines": len(lyrics)}
        if shifts:
            report["shifts"] = shifts
            report["score_shifted"] = round(score_shifted, 3)
        return lyrics, report

    def _split_long_lines(self, lyrics: List[Dict[str, Any]], va: Optional[VocalActivity],
                          max_dur: float = 4.5) -> List[Dict[str, Any]]:
        """
        把過長的行切在實際的換氣處。

        不同來源的 LRC 斷行習慣差很多，常把兩個樂句寫成一行。
        六秒長的一行在雙行 KTV 版面上會讓人抓不到走到哪，
        即使時間軸是準的，主觀上也像沒對準。
        """
        if va is None:
            return lyrics

        def split_once(line):
            words = line['words']
            n = len(words)
            if n < 6 or (line['end'] - line['start']) <= max_dur:
                return None

            span = line['end'] - line['start']
            # 優先切在 LRC 原本的空白處 —— 那是作詞者標出來的樂句邊界。
            # 沒有空白可切時，才退而求其次找句中最明顯的停頓。
            spaces = [k for k in range(2, n - 1) if words[k]['char'].isspace()]
            cands = spaces if spaces else (list(range(3, n - 2)) if span > 7.0 else [])

            best_k, best_score = None, -9.9
            for k in cands:
                t = words[k]['start']
                if t - line['start'] < 1.2 or line['end'] - t < 1.2:
                    continue
                silence = 1.0 - va.activity_mean(t - 0.22, t + 0.12)
                score = silence - 0.60 * abs((t - line['start']) / span - 0.5)
                if not spaces:
                    score -= 0.45          # 硬切沒有空白的句子要有真的停頓才值得
                if score > best_score:
                    best_k, best_score = k, score
            if best_k is None or best_score < -0.20:
                return None

            def trim(seq):
                # 只去頭尾空白，句中的空白要留著（那是排版上的頓點）
                a, b = 0, len(seq)
                while a < b and seq[a]['char'].isspace():
                    a += 1
                while b > a and seq[b - 1]['char'].isspace():
                    b -= 1
                return seq[a:b]

            left, right = trim(words[:best_k]), trim(words[best_k:])
            if len(left) < 2 or len(right) < 2:
                return None
            return [
                {'line_idx': 0, 'start': left[0]['start'], 'end': left[-1]['end'],
                 'text': "".join(w['char'] for w in left), 'words': left},
                {'line_idx': 0, 'start': right[0]['start'], 'end': line['end'],
                 'text': "".join(w['char'] for w in right), 'words': right},
            ]

        def expand(line, depth=0):
            if depth >= 3:
                return [line]
            pieces = split_once(line)
            if not pieces:
                return [line]
            return expand(pieces[0], depth + 1) + expand(pieces[1], depth + 1)

        out = []
        for line in lyrics:
            out.extend(expand(line))
        for i, ln in enumerate(out):
            ln['line_idx'] = i
        return out

    @staticmethod
    def _voiced_recall(lyrics: List[Dict[str, Any]], va: VocalActivity) -> float:
        """
        有多少比例的人聲被歌詞行覆蓋到。
        少了一段副歌、或整份歌詞只到 3/4 就沒了的 LRC，會在這裡現形。
        """
        if va is None or va.n_frames == 0 or not lyrics:
            return 0.0
        voiced = va.active
        total = float(voiced.sum())
        if total <= 0:
            return 0.0
        covered = np.zeros(va.n_frames, dtype=bool)
        for ln in lyrics:
            a = int(np.clip(round(ln['start'] / va.frame_sec), 0, va.n_frames))
            b = int(np.clip(round(ln['end'] / va.frame_sec), 0, va.n_frames))
            if b > a:
                covered[a:b] = True
        return float((voiced & covered).sum() / total)

    def _pick_best_lrc(self, cands: List[Dict[str, Any]], va: Optional[VocalActivity]):
        """
        每個候選都真的對齊一次，用聲學結果決定用哪一份。
        標題與曲長只是猜測，實際對得準不準要問音檔。
        """
        best = (None, None, -9.9)
        for c in cands:
            lyrics, report = self.align_lrc_to_audio(c['parsed'], va)
            if not lyrics:
                continue
            recall = self._voiced_recall(lyrics, va) if va is not None else 0.0
            total = report['score'] + 0.5 * recall + 0.02 * min(c['rank'], 5.0)
            report['recall'] = round(recall, 3)
            report['total'] = round(total, 3)
            report['track'] = f"{c['name']} / {c['artists']}"
            logger.info(f"  → {c['name']}: {len(lyrics)} 行, warp={report['score']:.3f}, "
                        f"覆蓋={recall:.3f}, 總分={total:.3f}")
            if total > best[2]:
                best = (lyrics, report, total)
        return best[0], best[1]

    # ------------------------------------------------------------------
    # 無 LRC 時的退路
    # ------------------------------------------------------------------
    def transcribe_pure_acoustic(self, vocal_audio_path: Path,
                                 va: Optional[VocalActivity] = None) -> List[Dict[str, Any]]:
        """找不到可信歌詞時直接聽人聲軌轉錄。精度不如 LRC 路徑，但總比沒有好。"""
        model = self._get_whisper_model()
        logger.info(f"改用純聲學轉錄: {Path(vocal_audio_path).name}")

        result = model.transcribe(
            str(vocal_audio_path),
            word_timestamps=True,
            language='zh',
            initial_prompt="繁體中文歌詞："
        )

        lyrics_data = []
        for seg in result.get("segments", []):
            seg_start = round(float(seg.get("start", 0)), 3)
            seg_end = round(float(seg.get("end", 0)), 3)
            raw_text = seg.get("text", "").strip()
            if not raw_text or (seg_end - seg_start) < 0.3:
                continue

            text_tw = self._to_traditional_chinese(raw_text)
            if self._is_non_singing(text_tw) and len(text_tw) < 12:
                continue

            # Whisper 的 word timestamp 在中文只到 token 級，用能量包絡把 token 內部再細分
            words_data = []
            for w in seg.get("words", []):
                w_text = self._to_traditional_chinese(w.get("word", "")).strip()
                w_s, w_e = float(w.get("start", 0)), float(w.get("end", 0))
                if w_text and w_e > w_s:
                    words_data.extend(self._distribute_chars(w_text, round(w_s, 3), round(w_e, 3), va))
            if not words_data:
                words_data = self._distribute_chars(text_tw, seg_start, seg_end, va)

            lyrics_data.append({
                "line_idx": len(lyrics_data),
                "start": seg_start,
                "end": seg_end,
                "text": "".join(w['char'] for w in words_data),
                "words": words_data
            })

        # Whisper 的 segment 常常一段十幾秒，同樣要切到可讀的長度
        return self._split_long_lines(lyrics_data, va)

    def _placeholder(self, track_name: str) -> List[Dict[str, Any]]:
        title = f"♪ {self._to_traditional_chinese(track_name)} ♪"
        return [
            {"line_idx": 0, "start": 5.0, "end": 12.0, "text": title,
             "words": self._distribute_chars(title, 5.0, 12.0, None)},
            {"line_idx": 1, "start": 15.0, "end": 25.0, "text": "請跟隨伴奏音樂盡情歡唱！",
             "words": self._distribute_chars("請跟隨伴奏音樂盡情歡唱！", 15.0, 25.0, None)},
        ]

    # ------------------------------------------------------------------
    def align(self, vocal_audio_path: Path, track_name: str, artist_name: str = "",
              output_json: Optional[Path] = None,
              local_lrc: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        `local_lrc`：使用者自己擺在檔案旁邊的那一份 LRC（本機匯入專用）。

        給了它就**不上網找**，而且不會因為對齊分數低就改用 Whisper ——
        理由見 local_import.py 第 4 點：使用者親手放的歌詞被默默換掉，
        他既看不到原因，也想不到要去哪裡改。它照樣走一次聲學校正
        （仿射 + 起唱點吸附），因為手打的 LRC 對到別的上傳版本時一樣會歪。
        """
        vocal_audio_path = Path(vocal_audio_path)

        va = None
        try:
            va = VocalActivity(vocal_audio_path)
        except Exception as e:
            logger.warning(f"人聲活動分析失敗，將只用 LRC 原始時間軸: {e}")

        lyrics: List[Dict[str, Any]] = []
        report: Dict[str, Any] = {"source": "none", "scale": 1.0, "offset": 0.0, "score": 0.0, "lines": 0}

        # 用實際音檔長度當比對依據，可以擋掉同名的翻唱／Live／加長版
        duration_hint = va.duration if va is not None else 0.0

        local_parsed = self.parse_lrc_with_timestamps(local_lrc) if local_lrc else []
        if local_lrc and len(local_parsed) < 2:
            # 放了檔案卻解不出兩行以上：多半是純文字歌詞（沒有時間戳）或編碼壞掉。
            # 這種情況要退回一般流程，不然整首歌會只剩一行字。
            logger.warning("旁邊那份 .lrc 解不出時間軸，改走一般流程")
            local_parsed = []

        if local_parsed:
            logger.info(f"採用檔案旁的 .lrc（{len(local_parsed)} 行），不上網搜尋")
            lyrics, report = self.align_lrc_to_audio(local_parsed, va)
            report["source"] = "lrc_local"
        else:
            cands = self.fetch_lrc_candidates(track_name, artist_name, duration_hint,
                                              max_candidates=3)
            if cands:
                picked, picked_report = self._pick_best_lrc(cands, va)
                if picked:
                    lyrics, report = picked, picked_report
            else:
                logger.warning("找不到任何有時間軸的 LRC")

        # 分數過低通常代表抓到的是同名的別首歌，寧可改聽人聲。
        # 使用者自己放的那一份不適用（他指定的就是這一份，換掉他不會知道）。
        if (lyrics and va is not None and report["source"] != "lrc_local"
                and report["score"] < MIN_TRUST_SCORE):
            logger.warning(f"LRC 對齊分數僅 {report['score']:.3f}，低於信任門檻，改用聲學轉錄")
            try:
                acoustic = self.transcribe_pure_acoustic(vocal_audio_path, va)
                if len(acoustic) >= 4:
                    lyrics = acoustic
                    report = {"source": "whisper", "scale": 1.0, "offset": 0.0,
                              "score": 0.0, "lines": len(lyrics)}
            except Exception as e:
                logger.warning(f"聲學轉錄失敗，沿用低分 LRC 結果: {e}")

        if not lyrics:
            try:
                lyrics = self.transcribe_pure_acoustic(vocal_audio_path, va)
                report = {"source": "whisper", "scale": 1.0, "offset": 0.0,
                          "score": 0.0, "lines": len(lyrics)}
            except Exception as e:
                logger.error(f"聲學轉錄失敗: {e}")

        if not lyrics:
            lyrics = self._placeholder(track_name)
            report = {"source": "placeholder", "scale": 1.0, "offset": 0.0, "score": 0.0, "lines": len(lyrics)}

        report["aligner"] = ALIGNER_VERSION
        logger.info(f"歌詞對齊完成: {report['lines']} 行，來源 {report['source']}")

        if output_json:
            output_json = Path(output_json)
            output_json.parent.mkdir(parents=True, exist_ok=True)
            # 先寫暫存檔再 rename。直接覆寫的話，中途斷電／磁碟滿會留下半份
            # lyrics.json —— 而 storage.get_song_lyrics 把 JSONDecodeError 吞掉回 []，
            # 快取清單又只看檔案存不存在，所以那首歌會永遠「完整但沒有歌詞」。
            # （「重算歌詞」讓覆寫這件事從一輩子一次變成隨時可按，風險跟著上升。）
            _write_json_atomic(output_json, lyrics)
            # 對齊診斷另存一份，方便事後查為什麼某首歌會歪
            _write_json_atomic(output_json.parent / "alignment.json", report)

        return lyrics
