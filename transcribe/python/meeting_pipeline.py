"""Audio-grounded review and word-to-speaker assignment, independent of model loading."""

from copy import deepcopy
from difflib import SequenceMatcher
import re


def tokens(text):
    return re.findall(r"\w+", text.casefold())


def add_flag(segment, flag):
    quality = segment.setdefault("asr_quality", {})
    flags = quality.setdefault("flags", [])
    if flag not in flags:
        flags.append(flag)
    quality["review_required"] = True


def coverage(start, end, intervals):
    """Fraction covered by the union, without double-counting intersecting regions."""
    if end <= start:
        return 0.0
    spans = sorted((max(start, s), min(end, e)) for s, e in intervals
                   if min(end, e) > max(start, s))
    total, previous = 0.0, start
    for s, e in spans:
        total += max(0.0, e - max(s, previous))
        previous = max(previous, e)
    return min(1.0, total / (end - start))


def assess_audio(segment, speech_regions):
    segment["asr_quality"]["speech_coverage"] = coverage(
        segment["start"], segment["end"], speech_regions)
    if segment["asr_quality"]["speech_coverage"] < 0.2:
        add_flag(segment, "text_outside_speech")
    words = segment.get("words", [])
    if any(w.get("probability") is not None and w["probability"] < 0.35 for w in words):
        add_flag(segment, "low_word_probability")
    if any(coverage(w["start"], w["end"], speech_regions) < 0.2 for w in words):
        add_flag(segment, "word_outside_speech")
    if any(w["end"] <= w["start"] or w["start"] < segment["start"] - 0.1
           or w["end"] > segment["end"] + 0.1 for w in words):
        add_flag(segment, "invalid_word_timestamps")


def needs_verification(segment):
    return bool(segment["asr_quality"].get("review_required")
                or re.search(r"\d", segment["text"]))


def record_verification(segment, alternatives, model_name):
    alternate_text = " ".join(s["text"] for s in alternatives).strip()
    original, alternate = tokens(segment["text"]), tokens(alternate_text)
    disagreement = 1.0 - SequenceMatcher(None, original, alternate, autojunk=False).ratio()
    segment["verification"] = dict(
        model=model_name, text=alternate_text, segments=alternatives,
        disagreement=disagreement, status="compared",
        # Agreement is evidence for review, never proof of correctness.
        automatically_accepted=False,
    )
    if disagreement > 0.25 or re.findall(r"\d+", segment["text"]) != re.findall(r"\d+", alternate_text):
        add_flag(segment, "verification_disagreement")


def assign_speaker(start, end, turns):
    scores = {}
    for turn in turns:
        overlap = max(0, min(end, turn["end"]) - max(start, turn["start"]))
        scores[turn["speaker"]] = scores.get(turn["speaker"], 0) + overlap
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if end <= start or not ranked or ranked[0][1] / (end - start) < 0.5:
        return "Unknown"
    if len(ranked) > 1 and ranked[1][1] >= ranked[0][1] * 0.8:
        return "Unknown"
    return ranked[0][0]


def continuous_word_speakers(words, timeline):
    """Bridge short uncovered word spans inside one ASR utterance, not meetings."""
    labels = [assign_speaker(w["start"], w["end"], timeline["turns"]) for w in words]
    inferred = set()
    index = 0
    while index < len(words):
        if labels[index] != "Unknown":
            index += 1
            continue
        end = index + 1
        while end < len(words) and labels[end] == "Unknown":
            end += 1
        start_time, end_time = words[index]["start"], words[end - 1]["end"]
        neighbors = []
        if index and 0 <= start_time - words[index - 1]["end"] <= .15:
            neighbors.append(labels[index - 1])
        if end < len(words) and 0 <= words[end]["start"] - end_time <= .15:
            neighbors.append(labels[end])
        candidates = set(neighbors) - {"Unknown"}
        if end_time - start_time <= 1.0 + 1e-6 and len(candidates) == 1 \
                and coverage(start_time, end_time, timeline.get("overlaps", [])) == 0:
            candidate = candidates.pop()
            competitors = [t for t in timeline["turns"] if t["speaker"] != candidate
                           and min(end_time, t["end"]) > max(start_time, t["start"])]
            if not competitors:
                for position in range(index, end):
                    labels[position] = candidate
                    inferred.add(position)
        index = end
    return labels, inferred


def attach_speakers(segments, timeline):
    """Split at word boundaries; never invent word times by distributing text."""
    results = []
    for index, original in enumerate(segments):
        segment = deepcopy(original)
        segment["diarization_backend"] = "pyannote-community-1"
        segment["source_segment"] = dict(index=index, start=original["start"], end=original["end"])
        if coverage(segment["start"], segment["end"], timeline.get("overlaps", [])) > 0:
            add_flag(segment, "overlapping_speech")
        words = segment.get("words", [])
        # Keep all decoder text if tokenization and the timestamp output differ.
        if not words or any(w["end"] <= w["start"] for w in words) \
                or tokens("".join(w["word"] for w in words)) != tokens(segment["text"]):
            segment["speaker"] = assign_speaker(segment["start"], segment["end"], timeline["turns"])
            add_flag(segment, "word_alignment_unavailable")
            groups = [segment]
        else:
            groups = []
            labels, inferred = continuous_word_speakers(words, timeline)
            for position, (word, speaker) in enumerate(zip(words, labels)):
                if not groups or groups[-1]["speaker"] != speaker:
                    group = deepcopy(segment)
                    group.update(start=word["start"], end=word["end"], speaker=speaker,
                                 words=[], text="")
                    groups.append(group)
                group = groups[-1]
                group["end"] = word["end"]
                group["words"].append(deepcopy(word))
                group["text"] += word["word"]
                if position in inferred:
                    group["words"][-1]["speaker_assignment"] = "utterance_context"
                    add_flag(group, "speaker_inferred_from_context")
            for group in groups:
                group["text"] = group["text"].strip()
        for group in groups:
            if group["speaker"] in timeline.get("uncertain_speakers", []):
                add_flag(group, "uncertain_cross_window_speaker")
            if group["speaker"] == "Unknown":
                add_flag(group, "uncertain_speaker")
            identity = timeline.get("identities", {}).get(group["speaker"])
            if identity:
                group["speaker_identity"] = identity
            results.append(group)
    return results
