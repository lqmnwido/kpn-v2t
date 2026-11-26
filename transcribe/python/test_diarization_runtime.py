"""Run with the isolated speaker Python; no weights or network are required."""

import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import soundfile as sf

from diarize_recording import diarize


@unittest.skipUnless(importlib.util.find_spec("pyannote") is not None,
                     "Run this test in the isolated diarization environment")
class DiarizationRuntimeTests(unittest.TestCase):
    def test_community_output_adapter_with_real_annotation_api(self):
        from pyannote.audio import Pipeline
        from pyannote.core import Annotation, Segment

        exclusive = Annotation()
        exclusive[Segment(0, 1.5), "a"] = "voice_a"
        exclusive[Segment(1.5, 3), "b"] = "voice_b"
        overlapping = Annotation()
        overlapping[Segment(0, 2), "a"] = "voice_a"
        overlapping[Segment(1, 3), "b"] = "voice_b"
        pipeline = MagicMock(return_value=SimpleNamespace(
            exclusive_speaker_diarization=exclusive, speaker_diarization=overlapping,
            speaker_embeddings=np.array([[1., 0.], [0., 1.]])))
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "sample.wav"
            sf.write(audio, np.zeros(48000, dtype=np.float32), 16000)
            with patch.object(Pipeline, "from_pretrained", return_value=pipeline), \
                    patch.dict(os.environ, {"NUM_SPEAKERS": "2", "MIN_SPEAKERS": "",
                                            "MAX_SPEAKERS": "", "SPEAKER_REFERENCES_PATH": "",
                                            "DIARIZATION_DEVICE": "cpu"}):
                result = diarize(str(audio))
        self.assertEqual([t["speaker"] for t in result["turns"]], ["Speaker_1", "Speaker_2"])
        self.assertEqual(result["overlaps"], [(1, 2)])
        self.assertEqual(pipeline.call_args.kwargs, {"num_speakers": 2})
        self.assertEqual(result["identities"], {})
        self.assertEqual(result["speaker_embeddings"]["Speaker_1"], [1., 0.])


if __name__ == "__main__":
    unittest.main()
