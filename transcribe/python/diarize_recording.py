"""Whole-recording Community-1 diarization. Run in its own Python environment."""

import argparse
import gc
import json
import os
from pathlib import Path
import sys

from runtime_paths import activate_dll_directories
activate_dll_directories(sys.prefix)

import numpy as np
import soundfile as sf

from meeting_pipeline import coverage


def validate_device(torch_module, device):
    if str(device).split(":")[0] == "cuda":
        if torch_module.version.cuda is None:
            raise RuntimeError(
                "DIARIZATION_DEVICE=cuda requires CUDA-enabled PyTorch in DIARIZATION_PYTHON; "
                f"this speaker runtime has torch {torch_module.__version__} (CPU-only). "
                "Install the CUDA speaker runtime or explicitly use DIARIZATION_DEVICE=cpu.")
        if not torch_module.cuda.is_available():
            raise RuntimeError("CUDA speaker runtime cannot access an NVIDIA GPU; check the driver and device access.")


def normalized(vector):
    vector = np.asarray(vector, dtype=np.float32)
    norm = np.linalg.norm(vector)
    if vector.shape != (192,) or not np.isfinite(vector).all() or norm < 1e-8:
        raise ValueError("Invalid ECAPA embedding")
    return vector / norm


def match_identity(embedding, references, threshold=0.75, margin=0.1):
    scores = sorted(((name, float(np.dot(normalized(embedding), normalized(ref))))
                     for name, ref in references.items()), key=lambda item: item[1], reverse=True)
    if not scores:
        return dict(name=None, status="unknown")
    name, score = scores[0]
    separation = score - scores[1][1] if len(scores) > 1 else 2.0
    accepted = score >= threshold and separation >= margin
    return dict(name=name if accepted else None, cosine_similarity=score,
                margin=separation, status="needs_confirmation" if accepted else "unknown")


def clean_samples(audio, turns, overlaps, speaker):
    samples, duration = [], 0.0
    for turn in turns:
        if turn["speaker"] != speaker or turn["end"] - turn["start"] < 2:
            continue
        if coverage(turn["start"], turn["end"], overlaps) > 0:
            continue
        # Several clean samples are safer than one very short or overlapping turn.
        end = min(turn["end"], turn["start"] + 10, turn["start"] + 30 - duration)
        sample = audio[int(turn["start"] * 16000):int(end * 16000)]
        if len(sample) < 32000:
            continue
        samples.append(sample)
        duration += len(sample) / 16000
        if duration >= 28:
            break
    return samples


def identify_speakers(audio, timeline, reference_path):
    import torch
    from speechbrain.inference.speaker import EncoderClassifier

    manifest_path = Path(reference_path).resolve()
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict):
        raise ValueError("Speaker references must map a person's name to a list of WAV paths")
    encoder = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb", run_opts={"device": "cpu"},
        savedir=os.environ.get("ECAPA_MODEL_DIR", "pretrained_models/ecapa"))

    def centroid(samples):
        embeddings = []
        for sample in samples:
            with torch.inference_mode():
                embedding = encoder.encode_batch(torch.from_numpy(sample).float().unsqueeze(0))
            embeddings.append(normalized(embedding.squeeze().cpu().numpy()))
        return normalized(np.mean(embeddings, axis=0))

    references = {}
    for name, paths in manifest.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(paths, list) or not paths:
            raise ValueError("Each enrolled person needs a name and at least one reference WAV")
        samples = []
        for path in paths:
            sample, rate = sf.read(manifest_path.parent / path, dtype="float32")
            if rate != 16000 or sample.ndim != 1 or len(sample) < 48000:
                raise ValueError("Reference WAVs must be mono 16 kHz, at least three seconds")
            samples.append(sample[:30 * 16000])
        references[name] = centroid(samples)

    identities = {}
    for speaker in sorted({turn["speaker"] for turn in timeline["turns"]}):
        samples = clean_samples(audio, timeline["turns"], timeline["overlaps"], speaker)
        identities[speaker] = (match_identity(
            centroid(samples), references,
            float(os.environ.get("SPEAKER_IDENTITY_THRESHOLD", "0.75")),
            float(os.environ.get("SPEAKER_IDENTITY_MARGIN", "0.10")),
        ) if samples else dict(name=None, status="insufficient_clean_speech"))
    return identities


def diarize(audio_path, windowed=False):
    os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "0")
    import torch
    device = os.environ.get("DIARIZATION_DEVICE", "cpu")
    validate_device(torch, device)
    from pyannote.audio import Pipeline

    audio, sample_rate = sf.read(audio_path, dtype="float32")
    if sample_rate != 16000 or audio.ndim != 1:
        raise ValueError("Diarization requires normalized mono 16 kHz audio")
    model = os.environ.get("DIARIZATION_MODEL", "pyannote/speaker-diarization-community-1")
    kwargs = {}
    if os.environ.get("HF_TOKEN"):
        kwargs["token"] = os.environ["HF_TOKEN"]
    pipeline = Pipeline.from_pretrained(model, **kwargs)
    batch_size = int(os.environ.get("DIARIZATION_BATCH_SIZE", "1"))
    if batch_size < 1:
        raise ValueError("DIARIZATION_BATCH_SIZE must be positive")
    pipeline.segmentation_batch_size = batch_size
    pipeline.embedding_batch_size = batch_size
    pipeline.to(torch.device(device))
    speaker_options = {}
    for setting in ("num_speakers", "min_speakers", "max_speakers"):
        if windowed and setting != "max_speakers":
            continue
        if os.environ.get(setting.upper()):
            value = int(os.environ[setting.upper()])
            if value < 1:
                raise ValueError(f"{setting} must be positive")
            speaker_options[setting] = value
    if windowed and "max_speakers" not in speaker_options and os.environ.get("NUM_SPEAKERS"):
        speaker_options["max_speakers"] = int(os.environ["NUM_SPEAKERS"])
    with torch.inference_mode():
        output = pipeline(dict(waveform=torch.from_numpy(audio).unsqueeze(0), sample_rate=sample_rate),
                          **speaker_options)
    mapping = {}
    turns = []
    for turn, _, label in output.exclusive_speaker_diarization.itertracks(yield_label=True):
        if label not in mapping:
            mapping[label] = f"Speaker_{len(mapping) + 1}"
        turns.append(dict(start=turn.start, end=turn.end, speaker=mapping[label]))
    overlaps = [(span.start, span.end)
                for span in output.speaker_diarization.get_overlap().support()]
    timeline = dict(backend="pyannote-community-1", model=model, turns=turns,
                    overlaps=overlaps, duration=len(audio) / sample_rate)
    embeddings = getattr(output, "speaker_embeddings", None)
    timeline["speaker_embeddings"] = {
        mapping[label]: embeddings[index].tolist()
        for index, label in enumerate(output.speaker_diarization.labels())
        if embeddings is not None and label in mapping
    }
    del pipeline, output
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    reference_path = os.environ.get("SPEAKER_REFERENCES_PATH")
    timeline["identities"] = identify_speakers(audio, timeline, reference_path) if reference_path else {}
    return timeline


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio-path", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--windowed", action="store_true")
    args = parser.parse_args()
    timeline = diarize(args.audio_path, windowed=args.windowed)
    target = Path(args.output)
    temporary = target.with_suffix(".tmp.json")
    temporary.write_text(json.dumps(timeline, ensure_ascii=False, allow_nan=False))
    temporary.replace(target)


if __name__ == "__main__":
    main()
