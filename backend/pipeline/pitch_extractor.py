import json
import logging
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger("KaraTube.PitchExtractor")

class PitchExtractor:
    def __init__(self, sr: int = 16000, hop_length: int = 512):
        self.sr = sr
        self.hop_length = hop_length

    def extract_pitch(self, vocal_audio_path: Path, output_json: Optional[Path] = None) -> Dict[str, Any]:
        """
        Fast F0 pitch extraction and note segmentation using YIN.
        """
        vocal_audio_path = Path(vocal_audio_path)
        logger.info(f"Extracting pitch from {vocal_audio_path.name}...")

        try:
            import librosa
            # Load audio at 16kHz for fast processing
            y, sr = librosa.load(str(vocal_audio_path), sr=self.sr, mono=True)
            duration = librosa.get_duration(y=y, sr=sr)

            # Compute RMS amplitude to filter unvoiced/silent frames
            hop = self.hop_length
            rms = librosa.feature.rms(y=y, hop_length=hop)[0]

            # Fast YIN pitch detection
            fmin = librosa.note_to_hz('C2') # ~65Hz
            fmax = librosa.note_to_hz('C7') # ~2093Hz
            f0 = librosa.yin(
                y,
                fmin=fmin,
                fmax=fmax,
                sr=sr,
                hop_length=hop
            )

            times = librosa.times_like(f0, sr=sr, hop_length=hop)

            # Threshold for voiced frame: RMS > 0.015 and reasonable f0
            pitch_points = []
            min_len = min(len(times), len(f0), len(rms))

            for i in range(min_len):
                t = float(times[i])
                freq = float(f0[i])
                energy = float(rms[i])

                if energy > 0.02 and fmin <= freq <= fmax:
                    midi = round(float(librosa.hz_to_midi(freq)), 1)
                    pitch_points.append([round(t, 2), midi])
                else:
                    pitch_points.append([round(t, 2), 0])

            # Simplify into continuous note blocks for the UI guide bar
            note_blocks = self._segment_into_notes(pitch_points)

            pitch_data = {
                "duration": round(duration, 2),
                "sample_rate": sr,
                "hop_length": self.hop_length,
                "notes": note_blocks,
                # Downsample raw points for network efficiency
                "points": pitch_points[::2]
            }

            if output_json:
                output_json = Path(output_json)
                output_json.parent.mkdir(parents=True, exist_ok=True)
                with open(output_json, 'w', encoding='utf-8') as f:
                    json.dump(pitch_data, f, indent=2)

            logger.info(f"Pitch extraction completed: {len(note_blocks)} note segments found.")
            return pitch_data

        except Exception as e:
            logger.error(f"Pitch extraction failed: {e}")
            fallback_data = {"duration": 0, "notes": [], "points": []}
            if output_json:
                with open(output_json, 'w', encoding='utf-8') as f:
                    json.dump(fallback_data, f, indent=2)
            return fallback_data

    def _segment_into_notes(self, pitch_points: List[List[float]], min_duration: float = 0.1) -> List[Dict[str, Any]]:
        """Cluster consecutive pitch points with similar pitch into note bars."""
        notes = []
        current_note = None

        for t, midi in pitch_points:
            if midi > 0:
                if current_note is None:
                    current_note = {
                        "start": t,
                        "end": t,
                        "midi_values": [midi]
                    }
                else:
                    avg_midi = np.median(current_note["midi_values"])
                    if abs(midi - avg_midi) <= 1.5 and (t - current_note["end"]) <= 0.25:
                        current_note["end"] = t
                        current_note["midi_values"].append(midi)
                    else:
                        if (current_note["end"] - current_note["start"]) >= min_duration:
                            notes.append({
                                "start": round(current_note["start"], 2),
                                "end": round(current_note["end"], 2),
                                "midi": int(round(np.median(current_note["midi_values"])))
                            })
                        current_note = {
                            "start": t,
                            "end": t,
                            "midi_values": [midi]
                        }
            else:
                if current_note is not None:
                    if (current_note["end"] - current_note["start"]) >= min_duration:
                        notes.append({
                            "start": round(current_note["start"], 2),
                            "end": round(current_note["end"], 2),
                            "midi": int(round(np.median(current_note["midi_values"])))
                        })
                    current_note = None

        if current_note and (current_note["end"] - current_note["start"]) >= min_duration:
            notes.append({
                "start": round(current_note["start"], 2),
                "end": round(current_note["end"], 2),
                "midi": int(round(np.median(current_note["midi_values"])))
            })

        return notes
