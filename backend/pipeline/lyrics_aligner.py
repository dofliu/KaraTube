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
import os
import re
import json
import logging
import requests
import urllib.parse
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from backend.pipeline.vocal_activity import VocalActivity

logger = logging.getLogger("KaraTube.LyricsAligner")

# --- 對齊參數 ---
SCALE_GRID = np.arange(0.95, 1.05001, 0.0025)   # 支援 ±5% 變速上傳
OFFSET_RANGE = 25.0                              # 片頭最多裁切/新增 25 秒
SNAP_WINDOW = 0.70                               # 行首吸附真實起唱點的搜尋半徑
ENERGY_WEIGHT = 0.70                             # 逐字時間：能量分配 vs 線性均分的混合比
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
        time_tag_pattern = re.compile(r'\[(\d{1,3}):(\d{2})(?:[.:](\d{2,3}))?\]')
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
    def _estimate_time_warp(self, times: np.ndarray, durations: np.ndarray,
                            va: VocalActivity) -> Tuple[float, float, float]:
        """
        在 (scale, offset) 空間網格搜尋，讓 LRC 行首盡量落在真實的人聲起點上。

        評分兩項：
          onset —— 行首之後有聲、之前無聲（真正的「起唱」特徵）
          cover —— 整行預期演唱區間確實有人聲
        """
        fs, N = va.frame_sec, va.n_frames
        cum = va.cum_active
        if N == 0 or times.size < 3:
            return 1.0, 0.0, 0.0

        def win_mean(start_sec: np.ndarray, length_frames) -> np.ndarray:
            a = np.clip(np.round(start_sec / fs).astype(np.int64), 0, N)
            b = np.clip(a + np.asarray(length_frames, dtype=np.int64), 0, N)
            return (cum[b] - cum[a]) / np.maximum(b - a, 1)

        pre_len = int(0.45 / fs)
        post_len = int(0.40 / fs)

        def score_for(scale: float, offsets: np.ndarray) -> np.ndarray:
            T = scale * times[None, :] + offsets[:, None]          # (O, L)
            cover_len = np.maximum(np.round(scale * durations / fs), 1).astype(np.int64)

            post = win_mean(T, post_len)
            pre = win_mean(T - 0.55, pre_len)
            onset = np.clip(post - pre, -1.0, 1.0).mean(axis=1)
            cover = win_mean(T, cover_len[None, :]).mean(axis=1)

            in_range = ((T >= -0.5) & (T <= va.duration)).mean(axis=1)
            s = 0.55 * onset + 0.45 * cover
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

    def _refine_line_times(self, mapped: List[float], char_counts: List[int],
                           va: Optional[VocalActivity]) -> Tuple[List[float], List[float]]:
        """行首吸附真實起唱點，行尾切在人聲停止處。"""
        L = len(mapped)
        starts = [float(x) for x in mapped]

        if va is not None:
            for i in range(L):
                floor = starts[i - 1] + 0.30 if i > 0 else -0.5
                cand = va.nearest_onset(starts[i], window=SNAP_WINDOW)
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
                e = va.voice_end_after(starts[i], max_gap=0.35, limit=hard)
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

    def _distribute_chars(self, text: str, start: float, end: float,
                          va: Optional[VocalActivity]) -> List[Dict[str, Any]]:
        """
        行內逐字時間。不再均分 —— 依累積人聲能量切點，
        換氣、長音、拖腔才會落在正確的字上面。
        """
        chars = list(text)
        n = len(chars)
        if n == 0:
            return []
        span = max(0.05, end - start)

        w = np.array([self._char_weight(c) for c in chars], dtype=np.float64)
        if w.sum() <= 0:
            w = np.ones(n)
        frac = (np.cumsum(w) / w.sum())[:-1]                      # n-1 個內部邊界

        linear = [start + span * f for f in frac]
        bounds = linear
        if va is not None and frac.size:
            e_pts = va.energy_split_times(start, end, frac.tolist())
            if e_pts:
                bounds = [ENERGY_WEIGHT * e + (1.0 - ENERGY_WEIGHT) * l
                          for e, l in zip(e_pts, linear)]

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
        for i, l in enumerate(out):
            l['line_idx'] = i
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
        for l in lyrics:
            a = int(np.clip(round(l['start'] / va.frame_sec), 0, va.n_frames))
            b = int(np.clip(round(l['end'] / va.frame_sec), 0, va.n_frames))
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
              output_json: Optional[Path] = None) -> List[Dict[str, Any]]:
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
        cands = self.fetch_lrc_candidates(track_name, artist_name, duration_hint, max_candidates=3)
        if cands:
            picked, picked_report = self._pick_best_lrc(cands, va)
            if picked:
                lyrics, report = picked, picked_report
        else:
            logger.warning("找不到任何有時間軸的 LRC")

        # 分數過低通常代表抓到的是同名的別首歌，寧可改聽人聲
        if lyrics and va is not None and report["score"] < MIN_TRUST_SCORE:
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

        logger.info(f"歌詞對齊完成: {report['lines']} 行，來源 {report['source']}")

        if output_json:
            output_json = Path(output_json)
            output_json.parent.mkdir(parents=True, exist_ok=True)
            with open(output_json, 'w', encoding='utf-8') as f:
                json.dump(lyrics, f, ensure_ascii=False, indent=2)
            # 對齊診斷另存一份，方便事後查為什麼某首歌會歪
            with open(output_json.parent / "alignment.json", 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)

        return lyrics
