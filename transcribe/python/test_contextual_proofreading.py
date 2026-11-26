from copy import deepcopy
import unittest
from unittest.mock import MagicMock, patch

import requests

from contextual_proofreading import (collect_proposals, apply_proposals, audio_support, ollama_suggest)


def document():
    text = "semua ahli tautan kuasa berbincang."
    words = [dict(start=i * .5, end=(i + 1) * .5, word=" " + word, probability=.9)
             for i, word in enumerate(text.split())]
    return dict(jobId="test", segments=[dict(segmentId="seg_1", start=0, end=2.5,
                speaker="Speaker_1", text=text, raw_text=" " + text, words=words,
                analysis_eligible=True, asr_quality=dict(flags=[], disposition="retained"))])


def edit(**changes):
    result = dict(segmentId="seg_1", original="tautan kuasa", replacement="jawatankuasa",
                  kind="spelling", reason="Contextual terminology")
    result.update(changes)
    return result


def support(*args):
    return dict(supported=True, words=[dict(start=1., end=2., word=" jawatankuasa", probability=.9)])


class ContextualProofreadingTests(unittest.TestCase):
    def test_model_originals_are_constrained_to_dynamic_source_spans(self):
        response = MagicMock()
        response.json.return_value = {"response": '{"edits": []}'}
        payload = dict(targetIds=["seg_1"], utterances=[dict(segmentId="seg_1", text="semua ahli tautan kuasa berbincang.")])
        with patch("contextual_proofreading.requests.post", return_value=response) as post:
            self.assertEqual(ollama_suggest("http://unused", "model", payload), [])
        properties = post.call_args.kwargs["json"]["format"]["properties"]["edits"]["items"]["properties"]
        self.assertIn("tautan kuasa", properties["original"]["enum"])
        self.assertNotIn("jawatankuasa", properties["original"]["enum"])
        self.assertNotIn("semua ahli tautan kuasa berbincang.", properties["original"]["enum"])
        self.assertEqual(properties["segmentId"]["enum"], ["seg_1"])

    def test_accepted_spelling_edit_preserves_source_and_speaker_times(self):
        source = document()
        before = deepcopy(source)
        result, audit = apply_proposals(source, [edit()], support)
        segment = result["segments"][0]
        self.assertEqual(segment["text"], "semua ahli jawatankuasa berbincang.")
        self.assertEqual(segment["raw_text"], source["segments"][0]["raw_text"])
        self.assertEqual(segment["asr_text"], source["segments"][0]["text"])
        self.assertEqual(segment["asr_words"], source["segments"][0]["words"])
        self.assertEqual(segment["speaker"], "Speaker_1")
        self.assertEqual((segment["start"], segment["end"]), (0, 2.5))
        self.assertEqual(segment["words"][2]["timing_method"], "original_phrase_span")
        self.assertEqual(audit["acceptedEdits"], 1)
        self.assertEqual(source, before)

    def test_llm_confidence_and_plausibility_do_not_replace_audio_support(self):
        source = document()
        result, audit = apply_proposals(source, [edit(confidence=1.)], lambda *args: dict(supported=False))
        self.assertEqual(result["segments"][0]["text"], source["segments"][0]["text"])
        self.assertEqual(result["segments"][0]["words"], source["segments"][0]["words"])
        self.assertIn("proofreading_suggestions", result["segments"][0])
        self.assertEqual(audit["edits"][0]["status"], "needs_review")

    def test_protected_entities_numbers_and_meaning_changes_are_not_auto_applied(self):
        verify = MagicMock()
        for proposal in (edit(kind="entity"), edit(kind="meaning"), edit(replacement="2026"),
                         edit(replacement="Ahmad"), edit(replacement="approved project changes")):
            with self.subTest(proposal=proposal):
                result, audit = apply_proposals(document(), [proposal], verify)
                self.assertEqual(audit["acceptedEdits"], 0)
                self.assertEqual(result["segments"][0]["text"], document()["segments"][0]["text"])
        verify.assert_not_called()

    def test_spelled_out_numbers_are_protected_even_if_model_calls_it_spelling(self):
        data = document()
        data["segments"][0]["text"] = "jumlahnya dua juta"
        verify = MagicMock()
        _, audit = apply_proposals(data, [edit(original="dua juta", replacement="dua juga")], verify)
        self.assertEqual(audit["acceptedEdits"], 0)
        self.assertEqual(audit["edits"][0]["validation"], "protected_entity_number_or_meaning")
        verify.assert_not_called()

    def test_invented_source_absent_quote_and_withheld_sources_are_rejected(self):
        verify = MagicMock()
        for proposal in (edit(segmentId="fake"), edit(original="not in recording")):
            _, audit = apply_proposals(document(), [proposal], verify)
            self.assertEqual(audit["acceptedEdits"], 0)
        data = document()
        data["segments"][0]["asr_quality"]["disposition"] = "withheld"
        _, audit = apply_proposals(data, [edit()], verify)
        self.assertEqual(audit["acceptedEdits"], 0)
        verify.assert_not_called()

    def test_collection_sends_context_and_restricts_target_ids(self):
        suggest = MagicMock(return_value=[edit(), edit(segmentId="invented")])
        proposals, report = collect_proposals(document(), suggest, "Agenda: progress meeting")
        self.assertEqual(len(proposals), 1)
        self.assertEqual(report["errors"], ["invalid_edit_or_target"])
        self.assertEqual(suggest.call_args.args[0]["meetingDocuments"], "Agenda: progress meeting")

    def test_unavailable_ollama_stops_after_one_request_without_losing_speech(self):
        data = document()
        for index in range(30):
            data["segments"].append(dict(data["segments"][0], segmentId=f"seg_extra_{index}"))
        suggest = MagicMock(side_effect=requests.Timeout())
        proposals, report = collect_proposals(data, suggest)
        self.assertEqual(proposals, [])
        self.assertEqual(report["status"], "unavailable")
        suggest.assert_called_once()

    def test_audio_candidate_must_be_confident_and_at_the_original_phrase_time(self):
        alternatives = [dict(words=[dict(start=1., end=2., word=" jawatankuasa", probability=.9)],
                             asr_quality=dict(avg_logprob=-.1, no_speech_prob=.01))]
        self.assertTrue(audio_support("jawatankuasa", alternatives, [(0, 3)], 1., 2.)["supported"])
        self.assertFalse(audio_support("jawatankuasa", alternatives, [(0, 3)], 4., 5.)["supported"])
        self.assertFalse(audio_support("jawatankuasa", alternatives, [], 1., 2.)["supported"])
        alternatives[0]["words"][0]["probability"] = .2
        self.assertFalse(audio_support("jawatankuasa", alternatives, [(0, 3)], 1., 2.)["supported"])

    def test_retry_does_not_apply_the_same_patch_twice(self):
        result, _ = apply_proposals(document(), [edit()], support)
        retry, audit = apply_proposals(result, [edit()], support)
        self.assertEqual(result, retry)
        self.assertEqual(audit["acceptedEdits"], 0)

    def test_repeated_recording_terms_generate_hints_without_replacement_rules(self):
        data = document()
        data["segments"][0]["text"] = "we discussed implemen tation"
        for index in range(3):
            data["segments"].append(dict(segmentId=f"repeat_{index}", text="implementation is important"))
        suggest = MagicMock(return_value=[])
        proposals, _ = collect_proposals(data, suggest)
        hints = suggest.call_args.args[0]["terminologyHints"]
        self.assertIn(dict(segmentId="seg_1", original="implemen tation", candidate="implementation"), hints)
        self.assertTrue(any(p["original"] == "implemen tation" and p["replacement"] == "implementation"
                            and p["proposedBy"] == "recording_vocabulary" for p in proposals))

    def test_entity_classification_cannot_be_bypassed_by_vocabulary_candidates(self):
        data = document()
        data["segments"][0]["text"] = "we discussed implemen tation"
        data["segments"].extend(dict(segmentId=f"repeat_{i}", text="implementation is important") for i in range(3))
        suggestion = edit(original="implemen tation", replacement="implementation", kind="entity")
        proposals, _ = collect_proposals(data, lambda payload: [suggestion])
        self.assertEqual(proposals, [suggestion])
        verify = MagicMock()
        _, audit = apply_proposals(data, proposals, verify)
        self.assertEqual(audit["acceptedEdits"], 0)
        verify.assert_not_called()

    def test_audio_check_cannot_drop_another_spoken_word(self):
        alternatives = [dict(words=[dict(start=0., end=1., word=" implementation", probability=.9),
                                   dict(start=1., end=1.5, word=" plan", probability=.9)],
                             asr_quality=dict(avg_logprob=-.1, no_speech_prob=.01))]
        self.assertFalse(audio_support("implementation", alternatives, [(0, 2)], 0., 1.5)["supported"])


if __name__ == "__main__":
    unittest.main()
