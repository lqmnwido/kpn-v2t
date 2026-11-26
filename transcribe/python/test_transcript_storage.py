import io
import json
import unittest
from unittest.mock import MagicMock

from transcript_storage import load_transcript


class Response(io.BytesIO):
    def release_conn(self):
        pass


class TranscriptStorageTests(unittest.TestCase):
    def test_analysis_restores_ids_without_reinserting_withheld_rows(self):
        speech = dict(start=4., end=5., speaker="Speaker_3", text="Baik.")
        process = dict(segments=[dict(start=0., end=1., speaker="Unknown", text="[tidak jelas]"),
                                 dict(speech, segmentId="seg_000003", words=[{"word": "Baik."}],
                                      asr_quality=dict(flags=[], review_required=False))])
        documents = {"results/job/transcript.json": dict(segments=[speech]),
                     "results/job/transcript.process.json": process}
        client = MagicMock()
        client.get_object.side_effect = lambda bucket, key: Response(json.dumps(documents[key]).encode())
        data = load_transcript(client, "bucket", "results/job/transcript.json")
        self.assertEqual(len(data["segments"]), 1)
        self.assertEqual(data["segments"][0]["segmentId"], "seg_000003")
        self.assertEqual(data["segments"][0]["words"], [{"word": "Baik."}])

    def test_public_edits_are_not_replaced_by_stale_process_predictions(self):
        documents = {"results/job/transcript.json": dict(segments=[dict(start=0., end=1., text="Corrected")]),
                     "results/job/transcript.process.json": dict(segments=[dict(start=0., end=1., text="Old")])}
        client = MagicMock()
        client.get_object.side_effect = lambda bucket, key: Response(json.dumps(documents[key]).encode())
        segment = load_transcript(client, "bucket", "results/job/transcript.json")["segments"][0]
        self.assertEqual(segment["text"], "Corrected")
        self.assertEqual(segment["segmentId"], "seg_000001")
        self.assertTrue(segment["asr_quality"]["review_required"])

    def test_chunks_are_read_without_fetching_an_unrelated_process_document(self):
        client = MagicMock()
        client.get_object.return_value = Response(b'{"segments": []}')
        self.assertEqual(load_transcript(client, "bucket", "results/job/chunk.json"), {"segments": []})
        client.get_object.assert_called_once()

    def test_display_numbering_keeps_original_evidence_ids(self):
        speech = dict(start=4., end=5., speaker="Speaker_1", text="Baik.")
        documents = {"results/job/transcript.json": dict(segments=[speech]),
                     "results/job/transcript.process.json": dict(
                         publishedSpeakerLabels={"Speaker_11": "Speaker_1"},
                         segments=[dict(speech, speaker="Speaker_11", segmentId="seg_000099")])}
        client = MagicMock()
        client.get_object.side_effect = lambda bucket, key: Response(json.dumps(documents[key]).encode())
        segment = load_transcript(client, "bucket", "results/job/transcript.json")["segments"][0]
        self.assertEqual(segment["speaker"], "Speaker_1")
        self.assertEqual(segment["sourceSpeaker"], "Speaker_11")
        self.assertEqual(segment["segmentId"], "seg_000099")
