import sys, json, whisper, zhconv, re, difflib
from pathlib import Path
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')

from backend.pipeline.lyrics_aligner import LyricsAligner
aligner = LyricsAligner()

def clean_cjk(s):
    if not s: return ""
    return re.sub(r'[^\u4e00-\u9fff]', '', zhconv.convert(str(s), 'zh-cn'))

def align_song(vocal_path, title, artist):
    model = whisper.load_model('base', device='cuda')
    # Use condition_on_previous_text=False to completely prevent repetitive hallucination loops
    res = model.transcribe(
        vocal_path,
        word_timestamps=True,
        language='zh',
        initial_prompt='繁體中文歌詞：',
        condition_on_previous_text=False,
        temperature=0.0
    )
    
    lrc_str = aligner.fetch_synced_lrc(title, artist)
    target_lines = aligner.extract_lyric_lines_from_lrc(lrc_str) if lrc_str else []
    
    cleaned_targets = []
    for l in target_lines:
        c = clean_cjk(l)
        if c and not aligner._is_non_singing(l):
            cleaned_targets.append((l, c))
            
    aligned_lines = []
    
    for seg in res.get('segments', []):
        raw_text = seg.get('text', '').strip()
        words = seg.get('words', [])
        
        # Extract CJK words
        cjk_words = [w for w in words if clean_cjk(w.get('word', ''))]
        if not cjk_words:
            if not clean_cjk(raw_text):
                continue
            seg_start = round(float(seg.get('start', 0)), 2)
            seg_end = round(float(seg.get('end', 0)), 2)
        else:
            seg_start = round(float(cjk_words[0].get('start', seg.get('start', 0))), 2)
            seg_end = round(float(cjk_words[-1].get('end', seg.get('end', 0))), 2)
            
        if seg_end - seg_start < 0.4:
            continue
            
        seg_cjk = clean_cjk(raw_text)
        if not seg_cjk or len(seg_cjk) < 2:
            continue
            
        # Match against target lines
        best_line = None
        best_score = 0.0
        
        for orig_line, target_cjk in cleaned_targets:
            common = sum(min(seg_cjk.count(c), target_cjk.count(c)) for c in set(seg_cjk))
            overlap = common / max(len(seg_cjk), len(target_cjk))
            ratio = difflib.SequenceMatcher(None, seg_cjk, target_cjk).ratio()
            
            score = max(ratio, overlap)
            if target_cjk in seg_cjk or seg_cjk in target_cjk:
                score = max(score, 0.65)
                
            if score > best_score:
                best_score = score
                best_line = orig_line
                
        # If match score is high enough (>= 0.38), use the clean Traditional Chinese lyric text
        if best_score >= 0.38 and best_line:
            final_text = zhconv.convert(best_line, 'zh-tw')
        elif len(seg_cjk) >= 4 and not aligner._is_non_singing(raw_text):
            final_text = zhconv.convert(raw_text, 'zh-tw')
        else:
            # Skip noise or non-matching chatter
            continue
            
        if aligner._is_non_singing(final_text):
            continue
            
        # Avoid duplicate consecutive lines at the same second
        if aligned_lines and aligned_lines[-1]['text'] == final_text and abs(aligned_lines[-1]['start'] - seg_start) < 2.0:
            continue
            
        chars = list(final_text)
        dur = max(0.2, seg_end - seg_start)
        dur_per_char = dur / max(1, len(chars))
        
        char_words = []
        for idx, ch in enumerate(chars):
            char_words.append({
                'char': ch,
                'start': round(seg_start + idx * dur_per_char, 2),
                'end': round(seg_start + (idx + 1) * dur_per_char, 2)
            })
            
        aligned_lines.append({
            'line_idx': len(aligned_lines),
            'start': seg_start,
            'end': seg_end,
            'text': final_text,
            'words': char_words
        })
        
    return aligned_lines

print("--- Testing 告白氣球 ---")
bu7_res = align_song('cache/songs/bu7nU9Mhpyo/vocals.mp3', '告白氣球', '周杰倫')
for l in bu7_res[:15]:
    print(f"[{l['start']}s - {l['end']}s] {l['text']}")
