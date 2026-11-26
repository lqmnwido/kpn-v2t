"""Keep acoustic risk out of published text while retaining raw ASR evidence."""

from copy import deepcopy
import math
import re

UNCLEAR_TEXT = "[tidak jelas]"
CONTENT_RISK_FLAGS = {
    "low_log_probability", "high_compression_ratio", "repeated_text",
    "possible_non_speech", "text_outside_speech", "verification_disagreement",
    "invalid_word_timestamps", "word_outside_speech", "low_word_probability",
    "invalid_timestamps", "verification_pending", "suspected_media_boilerplate",
}


def media_boilerplate(text):
    # Meeting-specific review triggers, not proof that these words were absent.
    # Do not flag ordinary thanks used in real meeting closings.
    normalized = " ".join(re.findall(r"\w+", text.casefold()))
    return any(phrase in normalized for phrase in (
        "terima kasih kerana menonton", "terima kasih telah menonton",
        "thanks for watching", "thank you for watching", "sari kata oleh",
        "jangan lupa like", "like share dan subscribe", "like share and subscribe",
    ))


def _number(value):
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def supported_words(segment):
    words = segment.get("words") or segment.get("raw_words", [])
    text = segment.get("withheld_text", segment.get("text", ""))
    if not words:
        return False
    if re.findall(r"\w+", "".join(w["word"] for w in words).casefold()) \
            != re.findall(r"\w+", text.casefold()):
        return False
    return all(w["end"] > w["start"]
               and w["start"] >= segment["start"] - .1
               and w["end"] <= segment["end"] + .1
               and _number(w.get("probability")) is not None
               and _number(w.get("probability")) >= .35 for w in words)


def supported_boundary_warning(segment, quality, flags):
    """VAD word boundaries are estimates, not evidence that a sentence is false."""
    if flags & CONTENT_RISK_FLAGS != {"word_outside_speech"}:
        return False
    speech = _number(quality.get("speech_coverage"))
    silence = _number(quality.get("no_speech_prob"))
    logprob = _number(quality.get("avg_logprob"))
    return (speech is not None and speech >= .6
            and silence is not None and silence <= .2
            and logprob is not None and logprob >= -.7
            and supported_words(segment))


def supported_non_speech_warning(segment, quality, flags):
    """A decoder silence score alone cannot outweigh strong speech evidence."""
    if flags & CONTENT_RISK_FLAGS != {"possible_non_speech"}:
        return False
    speech = _number(quality.get("speech_coverage"))
    silence = _number(quality.get("no_speech_prob"))
    logprob = _number(quality.get("avg_logprob"))
    return (speech is not None and speech >= .95
            and silence is not None and 0 <= silence <= .8
            and logprob is not None and logprob >= -.5
            and supported_words(segment))


def apply_acceptance(original):
    segment = deepcopy(original)
    quality = segment.setdefault("asr_quality", {})
    flags = set(quality.get("flags", []))
    if media_boilerplate(segment.get("text", "")) or media_boilerplate(segment.get("raw_text", "")):
        flags.add("suspected_media_boilerplate")
    if any(w["end"] <= w["start"] for w in segment.get("words", [])):
        flags.add("invalid_word_timestamps")
    quality["flags"] = sorted(flags)
    quality["acceptance_policy"] = "speech-evidence-v5"
    content_risks = flags & CONTENT_RISK_FLAGS
    retention_reason = None
    if supported_boundary_warning(segment, quality, flags):
        content_risks.discard("word_outside_speech")
        retention_reason = "supported_speech_with_vad_boundary_mismatch"
    elif supported_non_speech_warning(segment, quality, flags):
        content_risks.discard("possible_non_speech")
        retention_reason = "supported_speech_despite_decoder_non_speech_score"
    if retention_reason:
        quality["retention_reason"] = retention_reason
        quality["review_required"] = True
        if quality.get("disposition") == "withheld" and segment.get("withheld_text"):
            segment["text"] = segment.pop("withheld_text")
            segment["words"] = deepcopy(segment.get("raw_words", []))
            quality["previous_withheld_reasons"] = quality.pop("withheld_reasons", [])
            quality["disposition"] = "review"
    speech = _number(quality.get("speech_coverage"))
    no_speech = _number(quality.get("no_speech_prob"))
    words = segment.get("words", [])
    zero_duration_fraction = (sum(w["end"] <= w["start"] for w in words) / len(words)) if words else 0
    reasons = []
    # Keep acoustic reasons alongside the stricter publication decision.
    if speech is not None and speech <= 0.05:
        if no_speech is not None and no_speech >= 0.6:
            reasons.append("no_speech_and_no_audio_support")
        if words and zero_duration_fraction >= 0.5:
            reasons.append("invalid_words_and_no_audio_support")
        if "repeated_text" in quality.get("flags", []) and no_speech is not None and no_speech >= 0.4:
            reasons.append("repetition_and_no_audio_support")
    if speech is not None and speech < 0.5:
        if no_speech is not None and no_speech >= 0.6:
            reasons.append("high_non_speech_and_weak_audio_support")
        if words and zero_duration_fraction > 0:
            reasons.append("invalid_words_and_weak_audio_support")
    if "repeated_text" in quality.get("flags", []):
        reasons.append("decoding_repetition_requires_review")
    # Other content risks still fail closed; same-model agreement is not proof.
    reasons.extend("content_risk:" + flag for flag in sorted(content_risks))
    if reasons:
        # Idempotent on stored, already-withheld output.
        if quality.get("disposition") != "withheld":
            segment["withheld_text"] = segment.get("text", "")
            segment["raw_words"] = deepcopy(words)
        segment["text"] = UNCLEAR_TEXT
        segment["words"] = []
        quality["disposition"] = "withheld"
        quality["withheld_reasons"] = reasons
        quality["review_required"] = True
    elif quality.get("disposition") != "withheld":
        quality["disposition"] = "review" if quality.get("review_required") else "retained"
    segment["analysis_eligible"] = quality["disposition"] != "withheld" and not bool(content_risks)
    return segment


def analysis_segments(segments):
    """Re-evaluate older artifacts too; don't depend only on a new producer field."""
    return [prepared for segment in segments
            if (prepared := apply_acceptance(segment)).get("analysis_eligible")]
