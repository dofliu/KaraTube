import sys, json, whisper, zhconv, re, difflib
from pathlib import Path
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')

from backend.pipeline.lyrics_aligner import LyricsAligner
aligner = LyricsAligner()

def clean_cjk(s):
    if not s: return ""
    return re.sub(r'[^\u4e00-\u9fff]', '', zhconv.convert(str(s), 'zh-cn'))

def match_acoustic_segments(vocal_audio_path, target_lines, initial_title=""):
    model = whisper.load_model('base', device='cuda')
    prompt = f"繁體中文歌詞：{initial_title}" if initial_title else "繁體中文歌詞："
    res = model.transcribe(
        vocal_audio_path,
        word_timestamps=True,
        language='zh',
        initial_prompt=prompt,
        temperature=0.0
    )
    
    aligned_lines = []
    
    # Pre-clean target lines
    cleaned_targets = []
    for l in target_lines:
        c = clean_cjk(l)
        if c and not aligner._is_non_singing(l):
            cleaned_targets.append((l, c))
            
    for seg in res.get('segments', []):
        raw_text = seg.get('text', '').strip()
        words = seg.get('words', [])
        
        # Filter out empty or non-vocal segments
        cjk_words = [w for w in words if clean_cjk(w.get('word', ''))]
        if not cjk_words:
            # Check if raw_text has CJK
            if not clean_cjk(raw_text):
                continue
            seg_start = round(float(seg.get('start', 0)), 2)
            seg_end = round(float(seg.get('end', 0)), 2)
        else:
            # Use actual first CJK word start and last CJK word end
            seg_start = round(float(cjk_words[0].get('start', seg.get('start', 0))), 2)
            seg_end = round(float(cjk_words[-1].get('end', seg.get('end', 0))), 2)
            
        if seg_end - seg_start < 0.4:
            continue
            
        seg_cjk = clean_cjk(raw_text)
        if not seg_cjk:
            continue
            
        # Find best matching target line
        best_line = None
        best_score = 0.0
        
        for orig_line, target_cjk in cleaned_targets:
            # Calculate match score
            common = sum(min(seg_cjk.count(c), target_cjk.count(c)) for c in set(seg_cjk))
            overlap = common / max(len(seg_cjk), len(target_cjk))
            ratio = difflib.SequenceMatcher(None, seg_cjk, target_cjk).ratio()
            
            # Substring match bonus
            if target_cjk in seg_cjk or seg_cjk in target_cjk:
                score = max(ratio, overlap, 0.7)
            else:
                score = max(ratio, overlap)
                
            if score > best_score:
                best_score = score
                best_line = orig_line
                
        if best_score >= 0.35 and best_line:
            final_text = zhconv.convert(best_line, 'zh-tw')
        else:
            final_text = zhconv.convert(raw_text, 'zh-tw')
            
        # Discard non-singing lines (credits, composer, etc.)
        if aligner._is_non_singing(final_text) and len(final_text) < 15:
            continue
            
        # Generate word level timestamps for KTV sweep
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

# Test on bu7nU9Mhpyo (告白氣球)
lrc_str = aligner.fetch_synced_lrc('告白氣球', '周杰倫')
lrc_lines = aligner.extract_lyric_lines_from_lrc(lrc_str) if lrc_str else []
res = match_acoustic_segments('cache/songs/bu7nU9Mhpyo/vocals.mp3', lrc_lines, '周杰倫 告白氣球')

print("=== ACOUSTIC ANCHORED RESULT FOR 告白氣球 ===")
for l in res:
    print(f"[{l['start']}s - {l['end']}s] {l['text']}")
