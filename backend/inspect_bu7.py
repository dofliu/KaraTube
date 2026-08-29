import sys, json, whisper, zhconv, re
from pathlib import Path
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')

model = whisper.load_model('base', device='cuda')
res = model.transcribe('cache/songs/bu7nU9Mhpyo/vocals.mp3', word_timestamps=True, language='zh', initial_prompt='繁體中文歌詞：')

print('=== WHISPER RAW SEGMENTS ===')
for s in res.get('segments', [])[:15]:
    st = s.get('start', 0)
    et = s.get('end', 0)
    txt = s.get('text', '')
    print(f"[{st:.2f}s - {et:.2f}s] {txt}")
    w_list = [f"{w.get('word')}({w.get('start',0):.2f}-{w.get('end',0):.2f})" for w in s.get('words', [])]
    print("   Words:", " ".join(w_list))

from backend.pipeline.lyrics_aligner import LyricsAligner
aligner = LyricsAligner()
lrc_str = aligner.fetch_synced_lrc('告白氣球', '周杰倫')
print("\n=== FETCHED LRC LINES ===")
lines = aligner.extract_lyric_lines_from_lrc(lrc_str) if lrc_str else []
for idx, l in enumerate(lines[:10]):
    print(f"  Line {idx}: {l}")

print("\n=== CURRENT ALIGNMENT RESULT ===")
aligned = aligner.align('cache/songs/bu7nU9Mhpyo/vocals.mp3', '告白氣球', '周杰倫')
for idx, l in enumerate(aligned[:10]):
    print(f"  [{l['start']}s - {l['end']}s] {l['text']}")
