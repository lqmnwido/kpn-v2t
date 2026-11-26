import os
import sys
import warnings

# Silence all warnings (FutureWarning, UserWarning, etc.)
warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"
os.environ["KMP_WARNINGS"] = "0"
os.environ["TRITON_DEBUG"] = "0"

from dotenv import load_dotenv

# Load environment variables before ANY other imports
script_dir = os.path.dirname(os.path.abspath(__file__))
# ... (rest of path logic)
env_paths = [
    os.path.join(os.getcwd(), '.env'),
    os.path.join(script_dir, '.env'),
    os.path.join(script_dir, '..', '.env'),
    os.path.join(script_dir, '..', '..', '.env')
]

for p in env_paths:
    if os.path.exists(p):
        load_dotenv(dotenv_path=p)
        sys.stderr.write(f"--- Loaded .env from: {p} ---\n")
        break

import json
import torch
from copy import deepcopy
from pathlib import Path
from runtime_paths import venv_python

import logging
import numpy as np
import soundfile as sf
import io
import time
import requests
from minio import Minio
from faster_whisper import WhisperModel
from transcription_quality import transcribe_audio
from meeting_pipeline import assess_audio, attach_speakers, needs_verification, record_verification, add_flag
from faster_whisper.audio import decode_audio
from faster_whisper.vad import get_speech_timestamps as detect_speech, VadOptions
from transcription_quality import decoding_options
from diarization_windows import prepare_windows
from speaker_subprocess import run_speaker_command, speaker_environment
from transcript_acceptance import apply_acceptance
from contextual_proofreading import (collect_proposals, apply_proposals, ollama_suggest,
                                    audio_support, eligible)

# Configure minimal logging to stderr so it doesn't interfere with stdout pipes
logging.basicConfig(level=logging.ERROR)
logging.getLogger("speechbrain").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

# -----------------------------
# MODEL INITIALIZATION (Once)
# -----------------------------
is_cuda = torch.cuda.is_available()
whisper_device = "cuda" if is_cuda else "cpu"
speechbrain_device = "cuda:0" if is_cuda else "cpu"
compute_type = "int8_float16" if is_cuda else "int8"

# Keep the configured Faster-Whisper checkpoint and quantized execution.
model_name = os.environ.get("WHISPER_MODEL", "large-v3" if not is_cuda else "turbo")
sys.stderr.write(f"--- Initializing Whisper Model: {model_name} on {whisper_device} ---\n")
whisper_model = WhisperModel(model_name, device=whisper_device, compute_type=compute_type)

# Keep optional legacy ECAPA models off the GPU. Community-1 runs in a separate
# process/environment, so its NumPy/PyTorch requirements do not alter ASR.
encoder = None
vad_model = None
get_speech_timestamps = None
speechbrain_device = "cpu"


def ensure_legacy_models():
    global encoder, vad_model, get_speech_timestamps
    if encoder is None:
        from speechbrain.inference.speaker import EncoderClassifier
        encoder = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb", run_opts={"device": "cpu"})
        from silero_vad import load_silero_vad, get_speech_timestamps as timestamps
        vad_model = load_silero_vad()
        get_speech_timestamps = timestamps


def upload_json(task, key, payload):
    from urllib.parse import urlsplit
    url = urlsplit(task["minio_url"] if "://" in task["minio_url"] else "http://" + task["minio_url"])
    client = Minio(url.netloc, task["minio_access_key"], task["minio_secret_key"],
                   secure=url.scheme == "https")
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    client.put_object(task["minio_bucket"], key, io.BytesIO(data), len(data),
                      content_type="application/json")
    return {"status": "success", "key": key}


def prepare_recording(task):
    backend = os.environ.get("ASR_DIARIZATION_BACKEND", "pyannote")
    if backend not in ("ecapa", "pyannote"):
        raise ValueError("ASR_DIARIZATION_BACKEND must be pyannote or ecapa")
    if backend == "ecapa":
        timeline = {"backend": "ecapa", "turns": [], "overlaps": []}
    else:
        script = Path(__file__).with_name("diarize_recording.py")
        settings = dict(
            model=os.environ.get("DIARIZATION_MODEL", "pyannote/speaker-diarization-community-1"),
            window_seconds=float(os.environ.get("DIARIZATION_WINDOW_SECONDS", "600")),
            overlap_seconds=float(os.environ.get("DIARIZATION_OVERLAP_SECONDS", "15")),
            similarity_threshold=float(os.environ.get("DIARIZATION_SPEAKER_THRESHOLD", "0.70")),
            similarity_margin=float(os.environ.get("DIARIZATION_SPEAKER_MARGIN", "0.10")),
            num_speakers=os.environ.get("NUM_SPEAKERS", ""),
            max_speakers=os.environ.get("MAX_SPEAKERS", ""),
            identity_threshold=os.environ.get("SPEAKER_IDENTITY_THRESHOLD", "0.75"),
            identity_margin=os.environ.get("SPEAKER_IDENTITY_MARGIN", "0.10"),
        )
        reference_path = os.environ.get("SPEAKER_REFERENCES_PATH")
        if reference_path:
            import hashlib
            manifest = Path(reference_path).resolve()
            digest = hashlib.sha256(manifest.read_bytes())
            for paths in json.loads(manifest.read_text()).values():
                for path in paths:
                    with open(manifest.parent / path, "rb") as source:
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(block)
            settings["reference_fingerprint"] = digest.hexdigest()
        from urllib.parse import urlsplit
        from minio.error import S3Error
        url = urlsplit(task["minio_url"] if "://" in task["minio_url"] else "http://" + task["minio_url"])
        storage = Minio(url.netloc, task["minio_access_key"], task["minio_secret_key"],
                        secure=url.scheme == "https")
        prefix = f"results/{task['job_id']}/speaker_checkpoints/"

        def load_checkpoint(key):
            try:
                response = storage.get_object(task["minio_bucket"], prefix + key)
            except S3Error as error:
                if error.code == "NoSuchKey":
                    return None
                raise
            try:
                return json.loads(response.read())
            finally:
                response.close()
                response.release_conn()

        def save_checkpoint(key, result):
            upload_json(task, prefix + key, result)

        def progress(index, total, state):
            sys.stderr.write(f"--- Speaker window {index}/{total}: {state} ---\n")
            sys.stderr.flush()

        def run_window(wav, output, index, total):
            isolated_python = venv_python(Path(__file__).parent / "diarization-venv")
            default_python = str(isolated_python) if isolated_python.exists() else sys.executable
            timeout = int(os.environ.get("DIARIZATION_TIMEOUT_SECONDS", "5400"))
            if timeout <= 0:
                raise ValueError("DIARIZATION_TIMEOUT_SECONDS must be positive")
            command = [os.environ.get("DIARIZATION_PYTHON", default_python), str(script),
                       "--audio-path", str(wav), "--output", str(output), "--windowed"]
            run_speaker_command(command, output.with_suffix(".log"), timeout,
                                lambda: progress(index, total, "processing"),
                                speaker_environment(command[0]))

        # Avoid simultaneous GPU residency, including across the subprocess.
        if is_cuda:
            whisper_model.model.unload_model(to_cpu=True)
        try:
            timeline = prepare_windows(task["audio_path"], settings, run_window,
                                       load_checkpoint, save_checkpoint, progress)
        finally:
            if is_cuda:
                whisper_model.model.load_model()
    timeline["job_id"] = task["job_id"]
    return upload_json(task, f"results/{task['job_id']}/speaker_timeline.json", timeline)


def transcribe_task(task):
    offset = float(task.get("start_offset", 0))
    audio = decode_audio(task["audio_path"], sampling_rate=16000)
    detected = detect_speech(audio, VadOptions(threshold=0.5, speech_pad_ms=0,
                                              min_silence_duration_ms=500))
    raw = transcribe_audio(whisper_model, audio, offset, speech_timestamps=detected)
    results = deepcopy(raw)
    speech_regions = [(t["start"] / 16000 + offset, t["end"] / 16000 + offset)
                      for t in detected]
    for segment in results:
        assess_audio(segment, speech_regions)
    selected = [s for s in results if needs_verification(s)]
    limit = int(os.environ.get("ASR_MAX_REVIEW_SEGMENTS", "8"))
    if limit < 0:
        raise ValueError("ASR_MAX_REVIEW_SEGMENTS must be non-negative")
    reviewer_name = os.environ.get("ASR_REVIEW_MODEL", "").strip()
    reviewer = whisper_model
    if selected and limit and reviewer_name:
        if is_cuda:
            whisper_model.model.unload_model(to_cpu=True)
        try:
            reviewer = WhisperModel(reviewer_name, device=whisper_device, compute_type=compute_type)
        except Exception:
            if is_cuda:
                whisper_model.model.load_model()
            raise
    try:
        for index, segment in enumerate(selected):
            duration = segment["end"] - segment["start"]
            if index >= limit or not 0 < duration <= 25:
                segment["verification"] = {"status": "pending_review", "reason": "review_budget"}
                add_flag(segment, "verification_pending")
                continue
            start = max(0, int((segment["start"] - offset - 1) * 16000))
            end = min(len(audio), int((segment["end"] - offset + 1) * 16000))
            if end <= start:
                add_flag(segment, "invalid_timestamps")
                continue
            options = decoding_options()
            # A fresh decoding pass without glossary bias; still Faster-Whisper.
            options.update(beam_size=8, hotwords=None)
            retry, _ = reviewer.transcribe(audio[start:end], **options)
            from transcription_quality import segment_result
            alternatives = [r for s in retry
                            if (r := segment_result(s, offset + start / 16000)) is not None]
            record_verification(segment, alternatives, reviewer_name or model_name)
            segment["verification"]["different_checkpoint"] = bool(reviewer_name and reviewer_name != model_name)
    finally:
        if reviewer is not whisper_model:
            del reviewer
            import gc
            gc.collect()
            if is_cuda:
                whisper_model.model.load_model()
    # Evaluate complete ASR spans before word-to-speaker splitting, so an
    # unsupported phrase is withheld as a whole rather than published in parts.
    results = [apply_acceptance(segment) for segment in results]
    if task.get("timeline_path"):
        timeline = json.loads(Path(task["timeline_path"]).read_text())
        if timeline.get("job_id") != task.get("job_id"):
            raise ValueError("Speaker timeline belongs to a different job")
        if timeline["backend"] == "pyannote-community-1":
            results = attach_speakers(results, timeline)
    return {"status": "success", "segments": results, "raw_segments": raw}


def proofread_task(task):
    data = deepcopy(task["transcript"])
    if os.environ.get("ASR_PROOFREAD_ENABLED", "true").lower() in ("false", "0", "no"):
        data["proofreading"] = dict(status="disabled")
        return dict(status="success", transcript=data)
    if data.get("proofreading", {}).get("status") == "completed":
        return dict(status="success", transcript=data)
    if not any(eligible(s) for s in data.get("segments", [])):
        data["proofreading"] = dict(status="completed", eligibleSegments=0, acceptedEdits=0, edits=[])
        return dict(status="success", transcript=data)
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    model = os.environ.get("ASR_PROOFREAD_MODEL", "").strip() or os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
    budget = max(1, int(os.environ.get("ASR_PROOFREAD_BUDGET_SECONDS", "300")))
    deadline = time.monotonic() + budget
    proposal_deadline = time.monotonic() + budget / 2
    context = task.get("meeting_context", "")
    if is_cuda:
        whisper_model.model.unload_model(to_cpu=True)
    proposals, report = [], {}
    try:
        def suggest(payload):
            remaining = proposal_deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Proofreading budget exhausted")
            sys.stderr.write(f"--- Proofreading: {len(payload['targetIds'])} target segments ---\n")
            return ollama_suggest(host, model, payload, timeout=max(1, min(60, remaining)))
        proposals, report = collect_proposals(data, suggest, context, proposal_deadline)
    finally:
        # Ollama and Whisper must not compete for this machine's limited VRAM.
        try:
            requests.post(host.rstrip("/") + "/api/generate",
                          json={"model": model, "keep_alive": 0, "stream": False}, timeout=10).raise_for_status()
        except requests.RequestException:
            pass
        if is_cuda:
            whisper_model.model.load_model()

    sys.stderr.write(f"--- Proofreading: audio-checking {len(proposals)} proposed edits ---\n")

    def verify(start, end, replacement):
        if not 0 < end - start <= 25:
            return dict(supported=False, reason="phrase_duration")
        # Read only a small crop; never load the full meeting into RAM.
        with sf.SoundFile(task["audio_path"]) as recording:
            if recording.samplerate != 16000 or recording.channels != 1:
                raise ValueError("Proofreading requires mono 16 kHz normalized audio")
            crop_start = max(0, int((start - 2) * 16000))
            crop_end = min(len(recording), int((end + 2) * 16000))
            recording.seek(crop_start)
            audio = recording.read(max(0, crop_end - crop_start), dtype="float32")
        offset = crop_start / 16000
        if len(audio) < 160:
            return dict(supported=False, reason="empty_audio")
        speech = detect_speech(audio, VadOptions(threshold=.5, speech_pad_ms=0))
        if not speech:
            return dict(supported=False, reason="no_speech")
        options = decoding_options()
        options.update(beam_size=8, hotwords=None, initial_prompt=None, prefix=None)
        retry, _ = whisper_model.transcribe(audio, **options)
        from transcription_quality import segment_result
        alternatives = [r for s in retry if (r := segment_result(s, offset)) is not None]
        regions = [(s["start"] / 16000 + offset, s["end"] / 16000 + offset) for s in speech]
        evidence = audio_support(replacement, alternatives, regions, start, end)
        evidence.update(model=model_name, sameAsrFamily=True, promptedWithCorrection=False,
                        decodeText=" ".join(s["text"] for s in alternatives))
        return evidence

    data, changes = apply_proposals(data, proposals, verify, deadline)
    report.update(changes, model=model, method="contextual_proposals_with_audio_check")
    if report.get("status") == "unavailable" and changes["acceptedEdits"]:
        report["status"] = "partial"
    if any(e.get("validation") == "audio_review_budget" for e in changes["edits"]):
        report["status"] = "partial"
    data["proofreading"] = report
    sys.stderr.write(f"--- Proofreading: {changes['acceptedEdits']} accepted; status={report['status']} ---\n")
    return dict(status="success", transcript=data)


# -----------------------------
# GLOBAL SPEAKER GALLERY (across chunks)
# -----------------------------
# Maintains running centroids per speaker across all chunks in a job.
# Reset for each new job (when chunk_index == 1).
speaker_gallery = {}       # {speaker_id: centroid_embedding}
next_speaker_id = 1
SPEAKER_SIMILARITY_THRESHOLD = float(os.environ.get("SPEAKER_SIMILARITY_THRESHOLD", "0.50"))


def match_or_create_speaker(embedding):
    """
    Given an L2-normalized embedding, either:
    - Returns an existing speaker ID if cosine similarity >= threshold
    - Creates a new speaker ID otherwise
    Updates the running centroid on match (exponential moving average).
    """
    global speaker_gallery, next_speaker_id

    if not speaker_gallery:
        sid = f"Speaker_{next_speaker_id}"
        next_speaker_id += 1
        speaker_gallery[sid] = embedding.copy()
        return sid

    best_sid = None
    best_sim = -1.0
    for sid, centroid in speaker_gallery.items():
        sim = float(np.dot(embedding, centroid))  # cosine = dot product for L2-normed vectors
        if sim > best_sim:
            best_sim = sim
            best_sid = sid

    if best_sim >= SPEAKER_SIMILARITY_THRESHOLD and best_sid is not None:
        # Running EMA update: smooth centroid drift over time
        speaker_gallery[best_sid] = speaker_gallery[best_sid] * 0.7 + embedding * 0.3
        speaker_gallery[best_sid] /= (np.linalg.norm(speaker_gallery[best_sid]) + 1e-6)
        return best_sid

    # Below threshold — new speaker
    sid = f"Speaker_{next_speaker_id}"
    next_speaker_id += 1
    speaker_gallery[sid] = embedding.copy()
    return sid


def reset_speaker_gallery():
    """Reset gallery for a new job."""
    global speaker_gallery, next_speaker_id
    speaker_gallery = {}
    next_speaker_id = 1


def process_task(task):
    action = task.get("action")

    if action == "prepare_recording":
        return prepare_recording(task)

    if action == "proofread_transcript":
        return proofread_task(task)

    if action == "transcribe":
        return transcribe_task(task)

    elif action == "diarize":
        audio_path = task["audio_path"]
        segments = json.loads(task["segments_json"])
        if all(s.get("diarization_backend") == "pyannote-community-1" for s in segments):
            return upload_json(task, f"embeddings/{task['job_id']}/chunk-{task['chunk_index']:03d}.json", segments)
        ensure_legacy_models()
        offset = task.get("start_offset", 0.0)
        chunk_index = task.get("chunk_index", 0)

        # Reset the speaker gallery on the first chunk of each job
        if chunk_index == 1:
            reset_speaker_gallery()
            sys.stderr.write(f"--- Speaker gallery reset for new job: {task.get('job_id', '?')} ---\n")

        audio, fs = sf.read(audio_path)
        if len(audio.shape) > 1: audio = np.mean(audio, axis=1)

        results = []
        for seg in segments:
            s_idx = max(0, int((seg['start'] - offset) * 16000))
            e_idx = min(len(audio), int((seg['end'] - offset) * 16000))
            chunk = audio[s_idx:e_idx]
            if len(chunk) < 160:
                seg["speaker"] = "Unknown"
                results.append(seg)
                continue

            t_chunk = torch.from_numpy(chunk).float().to(speechbrain_device)
            speech_ts = get_speech_timestamps(t_chunk, vad_model, sampling_rate=16000)

            if not speech_ts:
                seg["speaker"] = "SILENCE"
            else:
                # Concatenate only speech parts for cleaner embedding
                speech_parts = []
                for ts in speech_ts:
                    speech_parts.append(t_chunk[ts['start']:ts['end']])

                if not speech_parts:
                    seg["speaker"] = "SILENCE"
                else:
                    speech_audio = torch.cat(speech_parts)
                    # Short speech is not silence; retain it without guessing a speaker.
                    if len(speech_audio) < 16000:
                        seg["speaker"] = "Unknown"
                    else:
                        with torch.no_grad():
                            emb = encoder.encode_batch(speech_audio.unsqueeze(0)).squeeze().cpu().numpy()
                        embedding = emb / (np.linalg.norm(emb) + 1e-6)
                        seg["embedding"] = embedding.tolist()
                        # Cross-chunk gallery matching
                        seg["speaker"] = match_or_create_speaker(embedding)
            results.append(seg)

        sys.stderr.write(
            f"--- Gallery after chunk {chunk_index:03d}: "
            f"{len(speaker_gallery)} speaker(s) ---\n"
        )

        return upload_json(task, f"embeddings/{task['job_id']}/chunk-{task['chunk_index']:03d}.json", results)

    return {"status": "error", "message": "Unknown action"}

# -----------------------------
# MAIN LOOP (Wait for commands)
# -----------------------------
if __name__ == "__main__":
    # Signal to Java that models are ready
    print(json.dumps({"status": "ready"}), flush=True)

    for line in sys.stdin:
        try:
            task = json.loads(line)
            response = process_task(task)
            print(json.dumps(response, allow_nan=False), flush=True)
        except Exception as e:
            print(json.dumps({"status": "error", "message": str(e)}), flush=True)
