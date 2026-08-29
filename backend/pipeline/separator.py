import os
import subprocess
import shutil
import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger("KaraTube.Separator")

class VocalSeparator:
    def __init__(self, model_name: str = "htdemucs", device: str = "cuda"):
        self.model_name = model_name
        self.device = device

    def separate(self, audio_path: Path, output_dir: Path) -> Tuple[Path, Path]:
        """
        Separates vocals and instrumental tracks.
        Returns (instrumental_path, vocals_path).
        """
        audio_path = Path(audio_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        final_instrumental = output_dir / "instrumental.mp3"
        final_vocals = output_dir / "vocals.mp3"

        # Check if already separated and cached
        if final_instrumental.exists() and final_vocals.exists():
            logger.info(f"Using cached stems for {audio_path.name}")
            return final_instrumental, final_vocals

        logger.info(f"Running Demucs separation on {audio_path} using model {self.model_name}...")

        # Run demucs separation
        temp_demucs_out = output_dir / "demucs_temp"
        temp_demucs_out.mkdir(parents=True, exist_ok=True)

        try:
            cmd = [
                "demucs",
                "--two-stems=vocals",
                "-n", self.model_name,
                "-d", self.device,
                "-o", str(temp_demucs_out),
                str(audio_path)
            ]
            logger.info(f"Executing: {' '.join(cmd)}")
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                logger.warning(f"Demucs warning/error: {res.stderr}")
                # Try fallback on CPU if GPU memory or CUDA had issues
                if self.device != "cpu":
                    logger.info("Retrying Demucs on CPU...")
                    cmd[cmd.index("-d") + 1] = "cpu"
                    subprocess.run(cmd, check=True)

            # Find output files
            # Structure is: temp_demucs_out / <model_name> / <track_name> / vocals.wav & no_vocals.wav
            stem_dir = temp_demucs_out / self.model_name / audio_path.stem
            vocals_wav = stem_dir / "vocals.wav"
            no_vocals_wav = stem_dir / "no_vocals.wav"

            if not vocals_wav.exists() or not no_vocals_wav.exists():
                # Search recursively in case folder name differs
                wav_files = list(temp_demucs_out.glob("**/*.wav"))
                for wf in wav_files:
                    if "no_vocals" in wf.name:
                        no_vocals_wav = wf
                    elif "vocals" in wf.name:
                        vocals_wav = wf

            if vocals_wav.exists() and no_vocals_wav.exists():
                # Convert to MP3
                subprocess.run([
                    'ffmpeg', '-y', '-i', str(no_vocals_wav),
                    '-acodec', 'libmp3lame', '-q:a', '2', str(final_instrumental)
                ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                subprocess.run([
                    'ffmpeg', '-y', '-i', str(vocals_wav),
                    '-acodec', 'libmp3lame', '-q:a', '2', str(final_vocals)
                ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                # Clean up temporary WAV stems to save disk space
                shutil.rmtree(temp_demucs_out, ignore_errors=True)
                logger.info(f"Separation completed successfully for {audio_path.name}")
                return final_instrumental, final_vocals
            else:
                raise FileNotFoundError("Demucs did not output expected stems.")

        except Exception as e:
            logger.error(f"Demucs separation failed: {e}. Utilizing fallback stereo vocal reduction...")
            # Fallback: Create instrumental via center channel cancellation and keep original as vocal
            self._fallback_separation(audio_path, final_instrumental, final_vocals)
            shutil.rmtree(temp_demucs_out, ignore_errors=True)
            return final_instrumental, final_vocals

    def _fallback_separation(self, audio_path: Path, inst_out: Path, voc_out: Path):
        """Fallback vocal extraction using FFmpeg audio filters (karaoke filter)."""
        # FFmpeg pan / stereotools filter for center channel cancellation (instrumental approximation)
        subprocess.run([
            'ffmpeg', '-y', '-i', str(audio_path),
            '-af', 'stereotools=mlev=0.01:slev=1.5:sbal=0',
            '-acodec', 'libmp3lame', '-q:a', '2', str(inst_out)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # For vocals fallback, copy original
        shutil.copyfile(audio_path, voc_out)
