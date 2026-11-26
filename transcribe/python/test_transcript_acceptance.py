from copy import deepcopy
import unittest

from meeting_pipeline import attach_speakers
from transcript_acceptance import apply_acceptance, analysis_segments


class AcceptanceTests(unittest.TestCase):
    def reported_speech(self):
        timings = [(81.76, 82.54, " dapat", .437988),
                   (82.54, 83.28, " dilaksanakan", .998169),
                   (83.28, 83.48, " dengan", .999023),
                   (83.48, 83.78, " lebih", .998047),
                   (83.78, 84.68, " terancang,", .990072),
                   (84.68, 85.72, " tersusun", .884888),
                   (85.72, 85.86, " dan", .969238),
                   (85.86, 85.98, " sebagainya.", .655624)]
        return dict(start=81.76, end=85.98,
                    text="dapat dilaksanakan dengan lebih terancang, tersusun dan sebagainya.",
                    words=[dict(start=s, end=e, word=w, probability=p) for s, e, w, p in timings],
                    asr_quality=dict(avg_logprob=-.285807, no_speech_prob=.628418,
                                     speech_coverage=1., review_required=True,
                                     flags=["possible_non_speech", "word_alignment_unavailable"]))

    def test_reported_speech_is_retained_despite_decoder_silence_warning(self):
        item = self.reported_speech()
        before = deepcopy(item)
        result = apply_acceptance(item)
        self.assertEqual(result["text"], item["text"])
        self.assertEqual(result["words"], item["words"])
        self.assertEqual(result["asr_quality"]["disposition"], "review")
        self.assertIn("possible_non_speech", result["asr_quality"]["flags"])
        self.assertTrue(result["analysis_eligible"])
        self.assertEqual(item, before)

    def test_stored_silence_only_rejection_can_be_restored_idempotently(self):
        item = self.reported_speech()
        text = item["text"]
        item.update(text="[tidak jelas]", withheld_text=text, raw_words=item["words"], words=[])
        item["asr_quality"].update(disposition="withheld",
                                 withheld_reasons=["content_risk:possible_non_speech"])
        result = apply_acceptance(item)
        self.assertEqual(result["text"], text)
        self.assertEqual(result["words"], item["raw_words"])
        self.assertEqual(apply_acceptance(result), result)

    def test_silence_warning_exception_requires_all_supporting_evidence(self):
        for field, value in (("speech_coverage", .8), ("speech_coverage", None),
                             ("avg_logprob", -.9), ("avg_logprob", None),
                             ("no_speech_prob", .9)):
            with self.subTest(field=field, value=value):
                item = self.reported_speech()
                item["asr_quality"][field] = value
                self.assertEqual(apply_acceptance(item)["text"], "[tidak jelas]")
        for flag in ("verification_disagreement", "repeated_text", "word_outside_speech"):
            item = self.reported_speech()
            item["asr_quality"]["flags"].append(flag)
            self.assertEqual(apply_acceptance(item)["text"], "[tidak jelas]")
        for mutation in ("weak", "zero_duration", "missing", "mismatch", "boilerplate"):
            item = self.reported_speech()
            if mutation == "weak":
                item["words"][0]["probability"] = .1
            elif mutation == "zero_duration":
                item["words"][0]["end"] = item["words"][0]["start"]
            elif mutation == "missing":
                item["words"] = []
            elif mutation == "mismatch":
                item["words"][0]["word"] = " sesuatu"
            else:
                item["raw_text"] = "Terima kasih kerana menonton!"
            self.assertEqual(apply_acceptance(item)["text"], "[tidak jelas]", mutation)

    def test_reported_thanks_hallucination_stays_withheld_at_full_speech_coverage(self):
        item = dict(start=420.72, end=421.98, text="Terima kasih.",
                    words=[dict(start=420.72, end=421.36, word=" Terima", probability=.87622),
                           dict(start=421.36, end=421.98, word=" kasih.", probability=.998535)],
                    asr_quality=dict(avg_logprob=-.5100446, no_speech_prob=.9458008,
                                     speech_coverage=1., review_required=True,
                                     flags=["possible_non_speech", "verification_disagreement",
                                            "word_alignment_unavailable"]),
                    verification=dict(text="Terima kasih kerana menonton!", disagreement=1/3,
                                      different_checkpoint=False))
        result = apply_acceptance(item)
        self.assertEqual(result["text"], "[tidak jelas]")
        self.assertFalse(result["analysis_eligible"])
        self.assertEqual(result["withheld_text"], "Terima kasih.")
        self.assertEqual(apply_acceptance(result), result)
        # The high decoder silence score also fails without retry disagreement.
        item["asr_quality"]["flags"].remove("verification_disagreement")
        self.assertEqual(apply_acceptance(item)["text"], "[tidak jelas]")

    def supported_sentence(self):
        timings = [(9.18, 9.74, " Tapi", .997), (9.74, 10.1, " Malaysia", .987),
                   (10.1, 10.36, " ni", .872), (10.36, 10.54, " Country", .498),
                   (10.54, 10.86, " for", 1.), (10.86, 11.04, " Old", .999),
                   (11.04, 11.24, " Men.", .982)]
        return dict(start=9.18, end=11.24, text="Tapi Malaysia ni Country for Old Men.",
                    words=[dict(start=s, end=e, word=w, probability=p) for s, e, w, p in timings],
                    asr_quality=dict(avg_logprob=-.129, no_speech_prob=.022,
                                     speech_coverage=.7805, review_required=True,
                                     flags=["word_outside_speech", "word_alignment_unavailable"]))

    def test_supported_sentence_survives_a_vad_word_boundary_mismatch(self):
        item = self.supported_sentence()
        result = apply_acceptance(item)
        self.assertEqual(result["text"], item["text"])
        self.assertEqual(result["words"], item["words"])
        self.assertEqual(result["asr_quality"]["disposition"], "review")
        self.assertIn("word_outside_speech", result["asr_quality"]["flags"])
        self.assertTrue(result["analysis_eligible"])

    def test_previous_timing_only_rejection_can_be_reassessed_without_new_asr(self):
        item = self.supported_sentence()
        text = item["text"]
        item.update(text="[tidak jelas]", withheld_text=text, raw_words=item["words"], words=[])
        item["asr_quality"].update(disposition="withheld",
                                    withheld_reasons=["content_risk:word_outside_speech"])
        result = apply_acceptance(item)
        self.assertEqual(result["text"], text)
        self.assertEqual(result["words"], item["raw_words"])
        self.assertEqual(apply_acceptance(result), result)
        self.assertNotIn("withheld_reasons", result["asr_quality"])

    def test_boundary_exception_does_not_override_other_content_risks(self):
        for flag in ("verification_disagreement", "invalid_word_timestamps",
                     "possible_non_speech", "low_word_probability", "suspected_media_boilerplate"):
            with self.subTest(flag=flag):
                item = self.supported_sentence()
                item["asr_quality"]["flags"].append(flag)
                self.assertEqual(apply_acceptance(item)["text"], "[tidak jelas]")
        item = self.supported_sentence()
        item["words"][0]["probability"] = .1
        self.assertEqual(apply_acceptance(item)["text"], "[tidak jelas]")

    def sample(self, text="Terima kasih kerana menonton!", speech=0., no_speech=.893):
        return dict(segmentId="seg_1", start=197.07, end=197.97, text=text,
                    words=[dict(start=197.07, end=197.97, word=text)],
                    asr_quality=dict(speech_coverage=speech, no_speech_prob=no_speech,
                                     flags=["text_outside_speech"], review_required=True),
                    verification=dict(disagreement=0, different_checkpoint=False))

    def test_reported_hallucination_is_withheld_even_when_retry_agrees(self):
        original = self.sample()
        before = deepcopy(original)
        result = apply_acceptance(original)
        self.assertEqual(result["text"], "[tidak jelas]")
        self.assertEqual(result["withheld_text"], original["text"])
        self.assertEqual(result["raw_words"], original["words"])
        self.assertEqual(result["words"], [])
        self.assertFalse(result["analysis_eligible"])
        self.assertEqual(original, before)
        self.assertEqual(apply_acceptance(result), result)
        split = attach_speakers([result], dict(turns=[], overlaps=[]))
        self.assertEqual(split[0]["text"], "[tidak jelas]")

    def test_supported_thanks_is_not_phrase_blacklisted(self):
        for text in ("Terima kasih.", "Terima kasih semua, Assalamualaikum."):
            item = self.sample(text, speech=1., no_speech=.01)
            item["asr_quality"].update(flags=["uncertain_speaker"], review_required=True)
            result = apply_acceptance(item)
            self.assertEqual(result["text"], text)
            self.assertTrue(result["analysis_eligible"])

    def test_vad_miss_is_withheld_for_review_without_erasing_raw_speech(self):
        item = self.sample("Mesyuarat pada 25 Oktober.", no_speech=.01)
        self.assertEqual(apply_acceptance(item)["withheld_text"], item["text"])
        self.assertEqual(analysis_segments([item]), [])

    def test_reported_high_coverage_hallucination_cannot_be_published(self):
        item = self.sample(speech=.732, no_speech=.326)
        item["asr_quality"]["flags"] = ["invalid_word_timestamps", "verification_disagreement"]
        result = apply_acceptance(item)
        self.assertEqual(result["text"], "[tidak jelas]")
        self.assertEqual(result["asr_quality"]["disposition"], "withheld")

    def test_confident_media_boilerplate_is_quarantined_for_meeting_review(self):
        for text in ("Terima kasih kerana menonton!", "Jangan lupa like, share dan subscribe",
                     "Thanks for watching.", "Sari kata oleh SDI Media"):
            item = self.sample(text, speech=1., no_speech=.01)
            item["asr_quality"].update(flags=[], review_required=False)
            result = apply_acceptance(item)
            self.assertEqual(result["withheld_text"], text)
            self.assertFalse(result["analysis_eligible"])
            self.assertIn("suspected_media_boilerplate", result["asr_quality"]["flags"])

    def test_all_content_risks_fail_closed_even_with_high_speech_coverage(self):
        from transcript_acceptance import CONTENT_RISK_FLAGS
        for flag in CONTENT_RISK_FLAGS:
            with self.subTest(flag=flag):
                item = self.sample("Tarikh ialah 25 Oktober.", speech=1., no_speech=.01)
                item["asr_quality"]["flags"] = [flag]
                self.assertEqual(apply_acceptance(item)["text"], "[tidak jelas]")

    def test_old_speaker_fragments_cannot_hide_boilerplate_in_raw_source(self):
        item = self.sample("Terima", speech=1., no_speech=.01)
        item["raw_text"] = "Terima kasih kerana menonton!"
        item["asr_quality"].update(flags=[], review_required=False)
        self.assertEqual(apply_acceptance(item)["text"], "[tidak jelas]")

    def test_partial_speech_and_invalid_word_times_require_review(self):
        item = self.sample(speech=.24, no_speech=.3)
        item["words"][0]["end"] = item["words"][0]["start"]
        item["asr_quality"]["flags"] = ["invalid_word_timestamps"]
        self.assertEqual(apply_acceptance(item)["asr_quality"]["disposition"], "withheld")

    def test_decoding_loop_is_withheld_but_raw_evidence_survives(self):
        item = self.sample("A, A, A, A, A, A", speech=1., no_speech=.01)
        item["asr_quality"]["flags"] = ["repeated_text"]
        self.assertEqual(apply_acceptance(item)["withheld_text"], item["text"])


if __name__ == "__main__":
    unittest.main()
