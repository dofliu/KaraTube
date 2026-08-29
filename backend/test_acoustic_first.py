import sys, json, whisper, zhconv, re, difflib
from pathlib import Path
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')

from backend.pipeline.lyrics_aligner import LyricsAligner
aligner = LyricsAligner()

def clean_cjk(s):
    return re.sub(r'[^\u4e00-\u9fff]', '', zhconv.convert(s, 'zh-cn'))

def fuzzy_match_lyric(seg_text, target_lines):
    seg_cjk = clean_cjk(seg_text)
    if not seg_cjk or len(seg_cjk) < 2:
        return None
    
    best_line = None
    best_score = 0.0
    
    for line in target_lines:
        line_cjk = clean_cjk(line)
        if not line_cjk: continue
        
        # Character overlap score
        common = sum(min(seg_cjk.count(c), line_cjk.count(c)) for c in set(seg_cjk))
        ratio = difflib.SequenceMatcher(None, seg_cjk, line_cjk).ratio()
        overlap_score = common / max(len(seg_cjk), len(line_cjk))
        score = max(ratio, overlap_score)
        
        if score > best_score:
            best_score = score
            best_line = line
            
    if best_score >= 0.35:
        return best_line, best_score
    return None

# Test on 告白氣球
model = whisper.load_model('base', device='cuda')
res = model.transcribe('cache/songs/bu7nU9Mhpyo/vocals.mp3', word_timestamps=True, language='zh', initial_prompt='繁體中文歌詞：周杰倫 告白氣球 塞納河畔 左岸的咖啡 我手一杯 品嘗你的美 留下唇印的嘴 你說你有點難追 想讓我知難而退 禮物不需挑最貴 只要香榭的落葉 親愛的 愛上你 從那天起 甜蜜的很輕易')

lrc_str = aligner.fetch_synced_lrc('告白氣球', '周杰倫')
lrc_lines = aligner.extract_lyric_lines_from_lrc(lrc_str) if lrc_str else []

print("=== NEW ACOUSTIC-FIRST ALIGNMENT FOR 告白氣球 ===")
aligned_result = []

for seg in res.get('segments', []):
    s_start = round(float(seg.get('start', 0)), 2)
    s_end = round(float(seg.get('end', 0)), 2)
    raw_text = seg.get('text', '').strip()
    
    # Filter out empty or music noise (less than 0.4s)
    if not raw_text or (s_end - s_start) < 0.4:
        continue
        
    match = fuzzy_match_lyric(raw_text, lrc_lines)
    if match:
        final_text = zhconv.convert(match[0], 'zh-tw')
    else:
        final_text = zhconv.convert(raw_text, 'zh-tw')
        
    if aligner._is_non_singing(final_text) and len(final_text) < 10:
        continue
        
    chars = list(final_text)
    dur_per_ch = (s_end - s_start) / max(1, len(chars))
    words = []
    for idx, ch in enumerate(chars):
        words.append({
            'char': ch,
            'start': round(s_start + idx * dur_per_ch, 2),
            'end': round(s_start + (idx + 1) * dur_per_ch, 2)
        })
        
    aligned_result.append({
        'line_idx': len(aligned_result),
        'start': s_start,
        'end': s_end,
        'text': final_text,
        'words': words
    })

print(f"Total lines: {len(aligned_result)}")
for l in aligned_result[:15]:
    print(f"  [{l['start']}s - {l['end']}s] {l['text']}")
