import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from diarize_recording import clean_samples, match_identity
from meeting_pipeline import (assess_audio, assign_speaker, attach_speakers, coverage,
                              needs_verification, record_verification)


def segment(text=" Selamat pagi. Baik."):
    return dict(start=28.0, end=30.0, text=text.strip(), raw_text=text,
                words=[dict(start=28.0, end=28.5, word=" Selamat", probability=0.9),
                       dict(start=28.5, end=29.0, word=" pagi.", probability=0.9),
                       dict(start=29.0, end=30.0, word=" Baik.", probability=0.9)],
                asr_quality=dict(flags=[], review_required=False))


class MeetingPipelineTests(unittest.TestCase):
    def test_reported_one_second_prefixes_join_their_utterance(self):
        for start, boundary, end, prefix, suffix, speaker in (
            (1241.4, 1242.4, 1242.92, "tengok", "compare dia", "Speaker_1"),
            (7813.14, 7814.14, 7814.78, "berapa", "sebenarnya", "Speaker_2"),
        ):
            with self.subTest(prefix=prefix):
                text = prefix + " " + suffix
                item = dict(start=start, end=end, text=text, raw_text=text,
                            words=[dict(start=start, end=boundary, word=prefix),
                                   dict(start=boundary, end=end, word=" " + suffix)],
                            asr_quality=dict(flags=[], review_required=False))
                timeline = dict(turns=[dict(start=boundary, end=end, speaker=speaker)], overlaps=[])
                result = attach_speakers([item], timeline)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["text"], text)
                self.assertEqual(result[0]["speaker"], speaker)
                self.assertEqual((result[0]["start"], result[0]["end"]), (start, end))

    def test_long_unassigned_prefix_is_not_bridged(self):
        item = dict(start=0., end=3., text="Tunggu saya", raw_text="Tunggu saya",
                    words=[dict(start=0., end=2., word="Tunggu"),
                           dict(start=2., end=3., word=" saya")],
                    asr_quality=dict(flags=[], review_required=False))
        timeline = dict(turns=[dict(start=2., end=3., speaker="Speaker_1")], overlaps=[])
        self.assertEqual([s["speaker"] for s in attach_speakers([item], timeline)],
                         ["Unknown", "Speaker_1"])

    def test_short_unknown_prefix_and_suffix_stay_with_the_utterance_speaker(self):
        for turn in ((8796.82, 8797.5), (8797.5, 8798.14)):
            with self.subTest(turn=turn):
                item = dict(start=8796.82, end=8798.14, text="Terima kasih", raw_text=" Terima kasih",
                            words=[dict(start=8796.82, end=8797.5, word=" Terima"),
                                   dict(start=8797.5, end=8798.14, word=" kasih")],
                            asr_quality=dict(flags=[], review_required=False))
                timeline = dict(turns=[dict(start=turn[0], end=turn[1], speaker="Speaker_2")], overlaps=[])
                result = attach_speakers([item], timeline)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["text"], "Terima kasih")
                self.assertEqual(result[0]["speaker"], "Speaker_2")
                self.assertIn("speaker_inferred_from_context", result[0]["asr_quality"]["flags"])
                self.assertNotIn("speaker_assignment", item["words"][0])

    def test_unknown_between_different_voices_is_not_guessed(self):
        item = segment("Selamat pagi. Baik.")
        timeline = dict(turns=[dict(start=28, end=28.5, speaker="Speaker_1"),
                               dict(start=29, end=30, speaker="Speaker_2")], overlaps=[])
        self.assertEqual([s["speaker"] for s in attach_speakers([item], timeline)],
                         ["Speaker_1", "Unknown", "Speaker_2"])

    def test_overlapping_and_competing_voice_evidence_prevents_gap_filling(self):
        item = segment()
        item["words"][1]["end"] = 28.9
        item["words"][2]["start"] = 28.9
        for timeline in (
            dict(turns=[dict(start=28, end=28.5, speaker="Speaker_1"),
                        dict(start=28.9, end=30, speaker="Speaker_1")], overlaps=[(28.5, 28.9)]),
            dict(turns=[dict(start=28, end=28.5, speaker="Speaker_1"),
                        dict(start=28.6, end=28.7, speaker="Speaker_2"),
                        dict(start=28.9, end=30, speaker="Speaker_1")], overlaps=[]),
        ):
            self.assertEqual(attach_speakers([item], timeline)[1]["speaker"], "Unknown")

    def test_word_level_speaker_change_preserves_text_and_raw_output(self):
        original = segment()
        timeline = dict(turns=[dict(start=0, end=29, speaker="Speaker_1"),
                               dict(start=29, end=60, speaker="Speaker_2")], overlaps=[])
        results = attach_speakers([original], timeline)
        self.assertEqual([s["speaker"] for s in results], ["Speaker_1", "Speaker_2"])
        self.assertEqual([s["text"] for s in results], ["Selamat pagi.", "Baik."])
        self.assertEqual((results[1]["start"], results[1]["end"]), (29, 30))
        self.assertNotIn("speaker", original)
        self.assertEqual(results[0]["raw_text"], original["raw_text"])

    def test_global_timeline_has_stable_speaker_across_chunks(self):
        timeline = dict(turns=[dict(start=0, end=100, speaker="Speaker_7")], overlaps=[])
        for offset in (0, 28, 56):
            item = segment()
            item.update(start=offset, end=offset + 2, words=[])
            self.assertEqual(attach_speakers([item], timeline)[0]["speaker"], "Speaker_7")

    def test_missing_or_mismatched_words_preserve_all_text(self):
        item = segment("Selamat pagi. Baik. Terima kasih.")
        timeline = dict(turns=[], overlaps=[])
        result = attach_speakers([item], timeline)[0]
        self.assertEqual(result["text"], item["text"])
        self.assertEqual(result["speaker"], "Unknown")
        self.assertIn("word_alignment_unavailable", result["asr_quality"]["flags"])

    def test_overlap_is_flagged_and_ambiguous_speaker_is_unknown(self):
        item = segment()
        timeline = dict(turns=[dict(start=28, end=30, speaker="Speaker_1")],
                        overlaps=[(28.5, 29.5)])
        self.assertIn("overlapping_speech", attach_speakers([item], timeline)[0]["asr_quality"]["flags"])
        self.assertEqual(assign_speaker(28.5, 29.5, [
            dict(start=28, end=29, speaker="Speaker_1"),
            dict(start=29, end=30, speaker="Speaker_2")]), "Unknown")

    def test_text_in_silence_is_retained_but_requires_review(self):
        item = segment()
        assess_audio(item, [])
        self.assertEqual(item["text"], "Selamat pagi. Baik.")
        self.assertIn("text_outside_speech", item["asr_quality"]["flags"])
        self.assertTrue(needs_verification(item))

    def test_union_does_not_double_count_speech(self):
        self.assertEqual(coverage(0, 10, [(0, 5), (2, 7)]), 0.7)

    def test_bundled_silero_detects_no_speech_in_real_silence(self):
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        self.assertEqual(get_speech_timestamps(np.zeros(16000, dtype=np.float32),
                                             VadOptions(speech_pad_ms=0)), [])

    def test_changed_date_is_flagged_without_replacing_primary(self):
        item = segment("Mesyuarat pada 25 Oktober.")
        record_verification(item, [dict(text="Mesyuarat pada 26 Oktober.")], "medium")
        self.assertEqual(item["text"], "Mesyuarat pada 25 Oktober.")
        self.assertIn("verification_disagreement", item["asr_quality"]["flags"])
        self.assertFalse(item["verification"]["automatically_accepted"])

    def test_identity_requires_threshold_and_margin(self):
        voice = [1.] + [0.] * 191
        other = [0., 1.] + [0.] * 190
        self.assertEqual(match_identity(voice, {"Ahmad": voice, "Ali": other})["name"], "Ahmad")
        self.assertIsNone(match_identity(voice, {"Ahmad": voice, "Ali": voice})["name"])
        self.assertIsNone(match_identity(voice, {"Ahmad": other})["name"])

    def test_short_and_overlapping_voice_samples_are_not_used_for_identity(self):
        audio = np.zeros(160000, dtype=np.float32)
        turns = [dict(start=0, end=1, speaker="A"), dict(start=2, end=5, speaker="A"),
                 dict(start=6, end=9, speaker="A")]
        samples = clean_samples(audio, turns, [(2, 5)], "A")
        self.assertEqual([len(s) for s in samples], [48000])


class WorkerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import the real worker with model creation replaced; no downloads/inference.
        with patch("faster_whisper.WhisperModel"), patch("torch.cuda.is_available", return_value=False):
            cls.worker = importlib.import_module("persistent_worker")

    def test_contextual_edit_is_checked_without_sending_the_candidate_as_an_asr_hint(self):
        from types import SimpleNamespace
        from test_contextual_proofreading import document, edit
        import soundfile as sf
        raw = SimpleNamespace(start=1., end=2., text=" jawatankuasa", avg_logprob=-.1,
                              no_speech_prob=.01, compression_ratio=1.,
                              words=[SimpleNamespace(start=1., end=2., word=" jawatankuasa", probability=.9)])
        model = MagicMock()
        model.transcribe.return_value = (iter([raw]), None)
        with tempfile.TemporaryDirectory() as directory:
            wav = str(Path(directory) / "audio.wav")
            sf.write(wav, np.zeros(5 * 16000), 16000)
            with patch.object(self.worker, "whisper_model", model), \
                    patch.object(self.worker, "is_cuda", True), \
                    patch.object(self.worker, "collect_proposals", return_value=([edit()], {"status": "completed"})), \
                    patch.object(self.worker.requests, "post"), \
                    patch.object(self.worker, "detect_speech", return_value=[dict(start=0, end=64000)]), \
                    patch.dict(os.environ, {"ASR_PROOFREAD_ENABLED": "true", "ASR_HOTWORDS": "jawatankuasa"}):
                result = self.worker.proofread_task(dict(transcript=document(), audio_path=wav))
        self.assertEqual(result["transcript"]["segments"][0]["text"], "semua ahli jawatankuasa berbincang.")
        self.assertEqual(result["transcript"]["proofreading"]["acceptedEdits"], 1)
        options = model.transcribe.call_args.kwargs
        self.assertIsNone(options["hotwords"])
        self.assertIsNone(options["initial_prompt"])
        self.assertIsNone(options["prefix"])
        model.model.unload_model.assert_called_once_with(to_cpu=True)
        model.model.load_model.assert_called_once()

    def test_unavailable_proofreader_keeps_text_and_restores_the_asr_model(self):
        from test_contextual_proofreading import document
        import requests
        model = MagicMock()
        with patch.object(self.worker, "whisper_model", model), \
                patch.object(self.worker, "is_cuda", True), \
                patch.object(self.worker, "ollama_suggest", side_effect=requests.Timeout()), \
                patch.object(self.worker.requests, "post"), \
                patch.dict(os.environ, {"ASR_PROOFREAD_ENABLED": "true"}):
            result = self.worker.proofread_task(dict(transcript=document(), audio_path="unused"))
        self.assertEqual(result["transcript"]["segments"], document()["segments"])
        self.assertEqual(result["transcript"]["proofreading"]["status"], "unavailable")
        model.model.load_model.assert_called_once()
        model.transcribe.assert_not_called()

    def test_silent_chunk_returns_no_text_and_does_not_retry(self):
        model = MagicMock()
        with patch.object(self.worker, "whisper_model", model), \
                patch.object(self.worker, "decode_audio", return_value=np.zeros(16000)), \
                patch.object(self.worker, "detect_speech", return_value=[]):
            result = self.worker.transcribe_task(dict(audio_path="unused", start_offset=196))
        self.assertEqual(result["segments"], [])
        self.assertEqual(result["raw_segments"], [])
        model.transcribe.assert_not_called()

    def test_primary_is_archived_and_retry_does_not_silently_correct_it(self):
        raw = segment("Mesyuarat pada 25 Oktober.")
        raw["words"] = []
        retry = MagicMock(text=" Mesyuarat pada 26 Oktober.", start=0.0, end=2.0,
                          avg_logprob=-0.1, no_speech_prob=0.01, compression_ratio=1.0, words=[])
        model = MagicMock()
        model.transcribe.return_value = (iter([retry]), None)
        with patch.object(self.worker, "whisper_model", model), \
                patch.object(self.worker, "decode_audio", return_value=np.zeros(3 * 16000)), \
                patch.object(self.worker, "transcribe_audio", return_value=[raw]), \
                patch.object(self.worker, "detect_speech", return_value=[]), \
                patch.dict(os.environ, {"ASR_REVIEW_MODEL": "", "ASR_MAX_REVIEW_SEGMENTS": "8"}):
            result = self.worker.transcribe_task(dict(audio_path="unused", start_offset=28))
        self.assertEqual(result["raw_segments"][0]["asr_quality"]["flags"], [])
        self.assertEqual(result["segments"][0]["text"], "[tidak jelas]")
        self.assertEqual(result["segments"][0]["withheld_text"], raw["text"])
        self.assertIn("verification_disagreement", result["segments"][0]["asr_quality"]["flags"])
        self.assertEqual(result["segments"][0]["verification"]["segments"][0]["start"], 28)

    def test_prepared_speakers_are_uploaded_without_legacy_reclustering(self):
        item = segment()
        item["diarization_backend"] = "pyannote-community-1"
        with patch.object(self.worker, "upload_json", return_value={"status": "success"}) as upload, \
                patch.object(self.worker, "ensure_legacy_models") as legacy:
            self.worker.process_task(dict(action="diarize", audio_path="unused",
                                          segments_json=json.dumps([item]), job_id="test", chunk_index=1))
        legacy.assert_not_called()
        self.assertEqual(upload.call_args.args[2][0]["diarization_backend"], "pyannote-community-1")

    def test_gpu_primary_is_restored_if_diarization_process_fails(self):
        model = MagicMock()
        with patch.object(self.worker, "is_cuda", True), patch.object(self.worker, "whisper_model", model), \
                patch.object(self.worker, "prepare_windows", side_effect=RuntimeError("failed")), \
                patch.object(self.worker, "Minio"), \
                patch.dict(os.environ, {"ASR_DIARIZATION_BACKEND": "pyannote"}):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                self.worker.prepare_recording(dict(audio_path="unused.wav", job_id="test", minio_url="http://unused",
                                                   minio_access_key="test", minio_secret_key="test"))
        model.model.unload_model.assert_called_once_with(to_cpu=True)
        model.model.load_model.assert_called_once()

    def test_timeline_from_another_job_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            timeline = Path(directory) / "timeline.json"
            timeline.write_text(json.dumps(dict(job_id="other", backend="pyannote-community-1", turns=[])))
            with patch.object(self.worker, "decode_audio", return_value=np.zeros(16000)), \
                    patch.object(self.worker, "transcribe_audio", return_value=[]), \
                    patch.object(self.worker, "detect_speech", return_value=[]):
                with self.assertRaisesRegex(ValueError, "different job"):
                    self.worker.transcribe_task(dict(audio_path="unused", timeline_path=str(timeline), job_id="test"))


if __name__ == "__main__":
    unittest.main()
