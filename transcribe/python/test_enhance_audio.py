import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import wave

import numpy as np

from enhance_audio import enhance, probe


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools required")
class AudioEnhancementTests(unittest.TestCase):
    def write_audio(self, path, silence=False):
        rate = 24000
        t = np.arange(rate * 4) / rate
        signal = np.zeros_like(t) if silence else .12 * np.sin(2 * np.pi * 300 * t) + .12 * np.sin(2 * np.pi * 30 * t)
        stereo = np.column_stack((signal, signal))
        with wave.open(str(path), "wb") as output:
            output.setparams((2, 2, rate, 0, "NONE", "not compressed"))
            output.writeframes((stereo * 32767).astype("<i2").tobytes())

    def test_real_ffmpeg_preserves_input_timing_and_reduces_relative_rumble(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.wav"
            self.write_audio(source)
            original = source.read_bytes()
            report = enhance(source, Path(directory) / "output")
            self.assertEqual(source.read_bytes(), original)
            for name, rate in (("asr", 16000), ("listening", 48000)):
                self.assertEqual(int(report["outputs"][name]["sample_rate"]), rate)
                self.assertLess(abs(report["outputs"][name]["duration"] - 4), .1)
                self.assertTrue(report["outputs"][name]["measured_two_pass"])
            with wave.open(str(Path(directory) / "output/audio-normalized.wav")) as audio:
                samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(float)
            samples = samples[16000:-16000]
            spectrum = np.abs(np.fft.rfft(samples))
            frequencies = np.fft.rfftfreq(len(samples), 1/16000)
            amplitude = lambda hz: spectrum[np.argmin(abs(frequencies - hz))]
            self.assertLess(amplitude(30) / amplitude(300), .35)
            stored = json.loads((Path(directory) / "output/audio-enhancement.json").read_text())
            self.assertEqual(stored, report)

    def test_digital_silence_remains_silent_without_nonfinite_filter_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "silence.wav"
            self.write_audio(source, silence=True)
            report = enhance(source, Path(directory) / "output")
            self.assertFalse(report["outputs"]["asr"]["measured_two_pass"])
            self.assertNotIn("measured_I=-inf", report["outputs"]["asr"]["filters"])
            with wave.open(str(Path(directory) / "output/audio-normalized.wav")) as audio:
                samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
            self.assertEqual(np.max(np.abs(samples)), 0)

    def test_custom_asr_filter_is_preserved_and_input_overwrite_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.wav"
            self.write_audio(source)
            report = enhance(source, Path(directory) / "output", audio_filter="volume=0.8")
            self.assertEqual(report["outputs"]["asr"]["filters"], "volume=0.8")
            self.assertFalse(report["automatic_profile"])
            with self.assertRaisesRegex(ValueError, "overwrite"):
                enhance(Path(directory) / "output/audio-normalized.wav", Path(directory) / "output")

    def test_inaccurate_container_duration_uses_decoded_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.wav"
            self.write_audio(source)

            def estimated_probe(path, ffprobe):
                info = probe(path, ffprobe)
                if Path(path) == source:
                    info["duration"] = 3.5
                return info

            with patch("enhance_audio.probe", side_effect=estimated_probe):
                report = enhance(source, Path(directory) / "output")
            self.assertEqual(report["source"]["reported_duration"], 3.5)
            self.assertEqual(report["source"]["duration"], 4)

    def test_duration_changing_filter_is_still_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.wav"
            self.write_audio(source)
            with self.assertRaisesRegex(ValueError, "asr enhancement changed duration"):
                enhance(source, Path(directory) / "output", audio_filter="atempo=2")


if __name__ == "__main__":
    unittest.main()
