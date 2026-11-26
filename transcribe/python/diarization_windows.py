"""Bounded speaker detection with durable per-window results and cautious matching."""

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import soundfile as sf


def fingerprint(audio_path, settings):
    digest = hashlib.sha256()
    with open(audio_path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    digest.update(json.dumps(settings, sort_keys=True).encode())
    return digest.hexdigest()


def unit(vector):
    array = np.asarray(vector, dtype=np.float32)
    norm = np.linalg.norm(array)
    return array / norm if array.ndim == 1 and np.isfinite(array).all() and norm > 1e-8 else None


def reconcile(result, gallery, next_id, threshold=0.7, margin=0.1):
    """Never merge two local speakers into one global speaker in the same window."""
    mapping, uncertain, used = {}, set(), set()
    embeddings = result.get("speaker_embeddings", {})
    labels = sorted({turn["speaker"] for turn in result["turns"]})
    candidates = []
    for label in labels:
        embedding = unit(embeddings.get(label, []))
        scores = sorted(((key, float(np.dot(embedding, centroid))) for key, centroid in gallery.items()
                         if embedding is not None and embedding.shape == centroid.shape),
                        key=lambda pair: pair[1], reverse=True)
        separation = scores[0][1] - scores[1][1] if len(scores) > 1 else 2.0
        candidates.append((scores[0][1] if scores else -1, separation, label, embedding, scores))
    for score, separation, label, embedding, scores in sorted(candidates, key=lambda item: item[0], reverse=True):
        if scores and score >= threshold and separation >= margin and scores[0][0] not in used:
            speaker = scores[0][0]
            gallery[speaker] = unit(gallery[speaker] * 0.9 + embedding * 0.1)
        else:
            speaker = f"Speaker_{next_id}"
            next_id += 1
            # An ambiguous cross-window match is kept separate for review.
            if scores or embedding is None:
                uncertain.add(speaker)
            if embedding is not None:
                gallery[speaker] = embedding
        mapping[label] = speaker
        used.add(speaker)
    return mapping, uncertain, next_id


def prepare_windows(audio_path, settings, run_window, load_checkpoint, save_checkpoint, progress):
    window_seconds = float(settings["window_seconds"])
    overlap = float(settings["overlap_seconds"])
    if window_seconds <= 0 or not 0 <= overlap < window_seconds / 2:
        raise ValueError("Speaker window must be positive with overlap less than half its length")
    info = sf.info(audio_path)
    if info.samplerate != 16000 or info.channels != 1:
        raise ValueError("Speaker windows require mono 16 kHz audio")
    identity = fingerprint(audio_path, settings)
    total = math.ceil(info.duration / window_seconds)
    if total == 0:
        raise ValueError("Cannot diarize empty audio")
    gallery, next_id, turns, overlaps, identities, uncertain = {}, 1, [], [], {}, set()
    work_dir = Path(audio_path).parent / "speaker-windows" / identity
    work_dir.mkdir(parents=True, exist_ok=True)
    for index in range(total):
        core_start = index * window_seconds
        core_end = min(info.duration, core_start + window_seconds)
        read_start = max(0, core_start - overlap)
        read_end = min(info.duration, core_end + overlap)
        checkpoint_key = f"{identity}/window-{index + 1:04d}.json"
        result = load_checkpoint(checkpoint_key)
        progress(index + 1, total, "cached" if result is not None else "started")
        if result is None:
            wav = work_dir / f"window-{index + 1:04d}.wav"
            output = wav.with_suffix(".json")
            with sf.SoundFile(audio_path) as source:
                source.seek(round(read_start * info.samplerate))
                audio = source.read(round((read_end - read_start) * info.samplerate), dtype="float32")
            sf.write(wav, audio, info.samplerate, subtype="PCM_16")
            try:
                run_window(wav, output, index + 1, total)
                result = json.loads(output.read_text())
                result["checkpoint"] = dict(fingerprint=identity, index=index, start=read_start, end=read_end)
                save_checkpoint(checkpoint_key, result)
            finally:
                # Only these derived per-window WAVs are removed; checkpoints persist.
                wav.unlink(missing_ok=True)
        expected = dict(fingerprint=identity, index=index, start=read_start, end=read_end)
        if result.get("checkpoint") != expected or result.get("backend") != "pyannote-community-1":
            raise ValueError("Invalid or mismatched speaker-window checkpoint")
        mapping, ambiguous, next_id = reconcile(
            result, gallery, next_id,
            float(settings["similarity_threshold"]), float(settings["similarity_margin"]))
        uncertain.update(ambiguous)
        for turn in result["turns"]:
            start, end = max(core_start, read_start + turn["start"]), min(core_end, read_start + turn["end"])
            if end > start:
                turns.append(dict(start=start, end=end, speaker=mapping[turn["speaker"]]))
        for start, end in result.get("overlaps", []):
            start, end = max(core_start, read_start + start), min(core_end, read_start + end)
            if end > start:
                overlaps.append((start, end))
        for local, candidate in result.get("identities", {}).items():
            global_id = mapping.get(local)
            if not global_id:
                continue
            previous = identities.get(global_id)
            if previous and previous.get("name") and candidate.get("name") \
                    and previous["name"] != candidate["name"]:
                identities[global_id] = dict(name=None, status="conflicting_window_matches")
            elif not previous or previous.get("status") == "insufficient_clean_speech":
                identities[global_id] = candidate
        progress(index + 1, total, "completed")
    return dict(backend="pyannote-community-1", model=settings["model"], duration=info.duration,
                turns=turns, overlaps=overlaps, identities=identities,
                uncertain_speakers=sorted(uncertain), window_count=total,
                checkpoint_fingerprint=identity, windowed=True)
