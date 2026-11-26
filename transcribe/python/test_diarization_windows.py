from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import soundfile as sf

from diarization_windows import prepare_windows, reconcile


class DiarizationWindowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.audio = Path(self.temporary.name) / "recording.wav"
        sf.write(self.audio, np.zeros(21 * 16000), 16000)
        self.settings = dict(model="community-1", window_seconds=10, overlap_seconds=2,
                             similarity_threshold=0.7, similarity_margin=0.1)
        self.saved = {}
        self.ran = []
        self.progress = []

    def run_window(self, wav, output, index, total):
        self.ran.append(index)
        duration = sf.info(wav).duration
        output.write_text(json.dumps(dict(backend="pyannote-community-1", duration=duration,
                                         turns=[dict(start=0, end=duration, speaker=f"local_{index}")],
                                         overlaps=[], speaker_embeddings={f"local_{index}": [1., 0.]}, identities={})))

    def prepare(self, runner=None):
        return prepare_windows(
            self.audio, self.settings, runner or self.run_window,
            lambda key: deepcopy(self.saved.get(key)),
            lambda key, result: self.saved.update({key: deepcopy(result)}),
            lambda *args: self.progress.append(args))

    def test_bounded_windows_have_global_times_and_consistent_speakers(self):
        result = self.prepare()
        self.assertEqual(self.ran, [1, 2, 3])
        self.assertEqual([(t["start"], t["end"]) for t in result["turns"]], [(0, 10), (10, 20), (20, 21)])
        self.assertEqual({t["speaker"] for t in result["turns"]}, {"Speaker_1"})
        self.assertEqual(list((self.audio.parent / "speaker-windows").rglob("*.wav")), [])

    def test_retry_reuses_completed_windows_after_interruption(self):
        def interrupted(wav, output, index, total):
            if index == 2:
                raise TimeoutError("interrupted window")
            self.run_window(wav, output, index, total)
        with self.assertRaises(TimeoutError):
            self.prepare(interrupted)
        self.assertEqual(len(self.saved), 1)
        self.ran.clear()
        result = self.prepare()
        self.assertEqual(self.ran, [2, 3])
        self.assertIn((1, 3, "cached"), self.progress)
        self.assertEqual(len(result["turns"]), 3)

    def test_changed_audio_does_not_reuse_old_checkpoint(self):
        self.prepare()
        self.ran.clear()
        sf.write(self.audio, np.ones(21 * 16000) * 0.1, 16000)
        self.prepare()
        self.assertEqual(self.ran, [1, 2, 3])

    def test_invalid_checkpoint_is_rejected(self):
        self.prepare()
        first_key = next(iter(self.saved))
        self.saved[first_key]["checkpoint"]["index"] = 999
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            self.prepare()

    def test_different_local_speakers_are_not_merged_into_one_global_voice(self):
        gallery = {"Speaker_1": np.array([1., 0.])}
        output = dict(turns=[dict(speaker="a"), dict(speaker="b")],
                      speaker_embeddings={"a": [1., 0.], "b": [0.99, 0.01]})
        mapping, uncertain, _ = reconcile(output, gallery, 2)
        self.assertNotEqual(mapping["a"], mapping["b"])
        self.assertTrue(uncertain)

    def test_ambiguous_voice_match_stays_separate_for_review(self):
        gallery = {"Speaker_1": np.array([1., 0.]), "Speaker_2": np.array([1., 0.])}
        output = dict(turns=[dict(speaker="a")], speaker_embeddings={"a": [1., 0.]})
        mapping, uncertain, _ = reconcile(output, gallery, 3)
        self.assertEqual(mapping["a"], "Speaker_3")
        self.assertIn("Speaker_3", uncertain)


if __name__ == "__main__":
    unittest.main()
