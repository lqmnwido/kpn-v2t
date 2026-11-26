import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import numpy as np

from transcription_quality import decoding_options, segment_result, transcribe_audio
import refine_speakers


class TranscriptionQualityTests(unittest.TestCase):
    def segment(self, text=" Terima kasih.", **overrides):
        fields = dict(text=text, start=1.0, end=2.0, avg_logprob=-0.2,
                      no_speech_prob=0.01, compression_ratio=1.0, words=[])
        fields.update(overrides)
        return SimpleNamespace(**fields)

    def test_legitimate_thanks_are_retained(self):
        result = segment_result(self.segment())
        self.assertEqual(result["text"], "Terima kasih.")
        self.assertEqual(result["raw_text"], " Terima kasih.")
        self.assertFalse(result["asr_quality"]["review_required"])

    def test_suspicious_repetition_is_preserved_and_flagged(self):
        text = "ya betul ya betul ya betul ya betul"
        result = segment_result(self.segment(text, avg_logprob=-1.5,
                                            no_speech_prob=0.9, compression_ratio=3))
        self.assertEqual(result["text"], text)
        self.assertEqual(set(result["asr_quality"]["flags"]), {
            "low_log_probability", "possible_non_speech", "high_compression_ratio",
            "repeated_text",
        })

    def test_global_word_and_segment_timestamps(self):
        word = SimpleNamespace(start=1.1, end=1.5, word=" baik", probability=0.8)
        result = segment_result(self.segment(words=[word]), offset=28)
        self.assertEqual((result["start"], result["end"]), (29, 30))
        self.assertEqual(result["words"][0]["start"], 29.1)
        self.assertEqual(result["words"][0]["probability"], 0.8)

    def test_language_and_glossary_configuration(self):
        self.assertEqual(decoding_options({})["language"], "ms")
        options = decoding_options({"ASR_LANGUAGE": "auto", "ASR_HOTWORDS": "JMM, CRIS"})
        self.assertIsNone(options["language"])
        self.assertTrue(options["multilingual"])
        self.assertEqual(options["hotwords"], "JMM, CRIS")
        self.assertNotIn("initial_prompt", options)

    def test_inference_iteration_error_is_not_partial_success(self):
        def segments():
            yield self.segment()
            raise RuntimeError("decoder failed")
        model = SimpleNamespace(transcribe=lambda *a, **kw: (segments(), None))
        with self.assertRaisesRegex(RuntimeError, "decoder failed"):
            transcribe_audio(model, "unused.wav")

    def test_no_speech_never_reaches_decoder(self):
        model = MagicMock()
        self.assertEqual(transcribe_audio(model, np.zeros(16000), speech_timestamps=[]), [])
        model.transcribe.assert_not_called()

    def test_speech_gate_preserves_full_conversation_context_and_global_times(self):
        model = MagicMock()
        model.transcribe.return_value = (iter([self.segment(start=1., end=2.)]), None)
        results = transcribe_audio(model, np.zeros(10 * 16000), offset=28,
                                   speech_timestamps=[dict(start=16000, end=32000),
                                                      dict(start=96000, end=112000)])
        options = model.transcribe.call_args.kwargs
        self.assertFalse(options["vad_filter"])
        self.assertNotIn("clip_timestamps", options)
        self.assertEqual(len(model.transcribe.call_args.args[0]), 10 * 16000)
        self.assertEqual(results[0]["start"], 29.)
        self.assertEqual(results[0]["asr_quality"]["input_policy"], "context-preserved-v3")

    def test_invalid_speech_intervals_do_not_reach_decoder(self):
        model = MagicMock()
        with self.assertRaisesRegex(ValueError, "Invalid VAD"):
            transcribe_audio(model, np.zeros(3 * 16000),
                             speech_timestamps=[dict(start=0, end=48001)])
        model.transcribe.assert_not_called()

    def test_nonfinite_signals_are_json_safe(self):
        result = segment_result(self.segment(avg_logprob=float("nan")))
        json.dumps(result, allow_nan=False)


class SpeakerRefinementTests(unittest.TestCase):
    def refine(self, segments):
        uploads = {}

        class Storage:
            def get_object(self, *args):
                return io.BytesIO(json.dumps({"segments": segments}).encode())

            def put_object(self, bucket, key, stream, length, **kwargs):
                uploads[key] = json.loads(stream.read())

        args = ["refine_speakers.py", "--job-id", "test", "--minio-url", "http://unused",
                "--minio-access-key", "test", "--minio-secret-key", "test",
                "--minio-bucket", "test", "--transcript-key", "transcript.json"]
        with patch.object(refine_speakers, "Minio", return_value=Storage()), \
                patch("sys.argv", args), patch("sys.stdout", new_callable=io.StringIO):
            refine_speakers.main()
        return uploads["transcript.json"]["segments"]

    def segment(self, duration, embedding=None):
        return dict(startSeconds=0, endSeconds=duration, text="baik",
                    speaker="Speaker_1", embedding=embedding)

    def test_missing_embeddings_do_not_imply_single_speaker(self):
        result = self.refine([self.segment(2), self.segment(3)])
        self.assertEqual([s["speaker"] for s in result], ["Unknown", "Unknown"])

    def test_one_embedding_does_not_identify_other_turns(self):
        result = self.refine([self.segment(2, [1.0] + [0.0] * 191), self.segment(3)])
        self.assertEqual([s["speaker"] for s in result], ["Speaker_1", "Unknown"])

    def test_brief_distinct_speaker_survives_refinement(self):
        result = self.refine([
            self.segment(100, [1.0] + [0.0] * 191),
            self.segment(1, [0.0, 1.0] + [0.0] * 190),
            self.segment(1),
        ])
        self.assertEqual([s["speaker"] for s in result],
                         ["Speaker_1", "Speaker_2", "Unknown"])

    def test_prepared_speaker_ids_and_uncertainty_text_survive_refinement(self):
        first = self.segment(2)
        first.update(speaker="Speaker_8", diarization_backend="pyannote-community-1",
                     text="[tidak jelas] Sila teruskan.")
        second = self.segment(2)
        second.update(speaker="Speaker_2", diarization_backend="pyannote-community-1")
        result = self.refine([first, second])
        self.assertEqual([s["speaker"] for s in result], ["Speaker_8", "Speaker_2"])
        self.assertEqual(result[0]["text"], "[tidak jelas] Sila teruskan.")


if __name__ == "__main__":
    unittest.main()
