"""Shared decoding and review metadata; never silently rewrite recognized speech."""

import math
import os
import re


def decoding_options(env=None):
    env = os.environ if env is None else env
    language = env.get("ASR_LANGUAGE", "ms").strip().lower()
    automatic = language in ("", "auto")
    return dict(
        language=None if automatic else language,
        multilingual=automatic,
        beam_size=5,
        temperature=0.0,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=500, threshold=0.5, speech_pad_ms=200
        ),
        word_timestamps=True,
        hallucination_silence_threshold=2.0,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.4,
        # Supply verified names/terms only; example sentences can bias decoding.
        hotwords=env.get("ASR_HOTWORDS", "").strip() or None,
    )


def _finite(value):
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def segment_result(segment, offset=0.0):
    """Keep decoder output and heuristic signals, including legitimate repetitions."""
    raw_text = segment.text
    text = raw_text.strip()
    if not text:
        return None
    avg_logprob = _finite(segment.avg_logprob)
    no_speech_prob = _finite(segment.no_speech_prob)
    compression_ratio = _finite(segment.compression_ratio)
    flags = []
    if avg_logprob is not None and avg_logprob < -1.0:
        flags.append("low_log_probability")
    if no_speech_prob is not None and no_speech_prob > 0.6:
        flags.append("possible_non_speech")
    if compression_ratio is not None and compression_ratio > 2.4:
        flags.append("high_compression_ratio")
    tokens = re.findall(r"\w+", text.casefold())
    for width in (1, 2, 3):
        if any(
            tokens[i:i + width] * 4 == tokens[i:i + width * 4]
            for i in range(len(tokens) - width * 4 + 1)
        ):
            flags.append("repeated_text")
            break
    if segment.end <= segment.start:
        flags.append("invalid_timestamps")
    return dict(
        start=segment.start + offset,
        end=segment.end + offset,
        text=text,
        raw_text=raw_text,
        words=[dict(
            start=w.start + offset, end=w.end + offset,
            word=w.word, probability=_finite(w.probability),
        ) for w in (segment.words or [])],
        asr_quality=dict(
            avg_logprob=avg_logprob, no_speech_prob=no_speech_prob,
            compression_ratio=compression_ratio,
            review_required=bool(flags), flags=flags,
        ),
    )


def transcribe_audio(model, audio_path, offset=0.0, speech_timestamps=None):
    options = decoding_options()
    if speech_timestamps is not None:
        # An empty clip list means "decode everything" in Faster-Whisper.
        # Return before invoking ASR when the independent detector found nothing.
        if not speech_timestamps:
            return []
        for span in speech_timestamps:
            if not 0 <= span["start"] < span["end"] <= len(audio_path):
                raise ValueError("Invalid VAD speech interval")
        # VAD is a chunk-level gate. Preserve the complete bounded ASR chunk so
        # short utterances retain conversational context instead of decoding
        # every subsecond VAD island independently. Time remains source-relative.
        options.update(vad_filter=False)
    segments, _ = model.transcribe(audio_path, **options)
    # Iteration performs inference too; let callers handle errors from both stages.
    results = [result for segment in segments
               if (result := segment_result(segment, offset)) is not None]
    if speech_timestamps is not None:
        for result in results:
            result["asr_quality"]["input_policy"] = "context-preserved-v3"
    return results
