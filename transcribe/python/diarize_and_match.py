import os
import sys
import json
import argparse
import torch
import numpy as np
import soundfile as sf
import io
import warnings
from minio import Minio

warnings.filterwarnings("ignore")

# -----------------------------
# MODEL
# -----------------------------
def load_embedding_model():
    from speechbrain.inference.speaker import EncoderClassifier
    return EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        run_opts={"device": "cpu"}
    )

# -----------------------------
# VAD
# -----------------------------
# Global VAD model
vad_model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                                  model='silero_vad',
                                  force_reload=False,
                                  onnx=False)
(get_speech_timestamps, _, read_audio, _, _) = utils

# -----------------------------
# EMBEDDING EXTRACTION
# -----------------------------
def get_embedding(audio, start, end, model):
    # Ensure indices are within bounds and valid
    s = max(0, int(start * 16000))
    e = min(len(audio), int(end * 16000))
    
    if e <= s: 
        return None

    seg = audio[s:e]
    if len(seg) < 160: # Less than 10ms is useless
        return None

    if len(seg.shape) > 1: seg = np.mean(seg, axis=1)

    # Silero VAD Detection
    speech_timestamps = get_speech_timestamps(torch.from_numpy(seg).float(), vad_model, sampling_rate=16000)
    if not speech_timestamps:
        return "SILENCE"
    
    # Volume Normalization
    max_val = np.max(np.abs(seg))
    seg = seg / (max_val + 1e-6) 

    with torch.no_grad():
        t = torch.from_numpy(seg).float().unsqueeze(0)
        emb = model.encode_batch(t).squeeze().cpu().numpy()

    emb = emb / (np.linalg.norm(emb) + 1e-6)
    return emb.astype("float32")

# -----------------------------
# MAIN
# -----------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio-path", required=True)
    parser.add_argument("--segments-file", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--chunk-index", type=int, required=True)
    parser.add_argument("--start-offset", type=float, default=0.0)
    parser.add_argument("--minio-url", required=True)
    parser.add_argument("--minio-access-key", required=True)
    parser.add_argument("--minio-secret-key", required=True)
    parser.add_argument("--minio-bucket", required=True)
    args = parser.parse_args()

    client = Minio(
        args.minio_url.replace("http://", "").replace("https://", ""),
        access_key=args.minio_access_key,
        secret_key=args.minio_secret_key,
        secure=args.minio_url.startswith("https"),
    )

    prefix = f"embedded_files/{args.job_id}/"

    # 0. Cleanup if this is the first chunk
    if args.chunk_index == 1:
        try:
            objects = client.list_objects(args.minio_bucket, prefix=prefix, recursive=True)
            for obj in objects:
                client.remove_object(args.minio_bucket, obj.object_name)
        except: pass

    # 1. Load Audio
    audio, sr = sf.read(args.audio_path)
    if len(audio.shape) > 1: audio = np.mean(audio, axis=1)
    if sr != 16000:
        import librosa
        audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)

    total_duration = len(audio) / 16000.0
    model = load_embedding_model()

    # 2. Level 1: Micro-chunking (Increased for stability)
    win_size = 3.0
    step_size = 1.0
    micro_embeddings = []
    
    for start in np.arange(0, total_duration - 1.0, step_size):
        end = min(start + win_size, total_duration)
        emb = get_embedding(audio, start, end, model)
        if isinstance(emb, np.ndarray):
            micro_embeddings.append({"start": start, "end": end, "embedding": emb})

    # 3. Load history
    speaker_centroids = {}
    max_id = 0
    history_key = f"embedded_files/{args.job_id}/centroids.json"
    try:
        response = client.get_object(args.minio_bucket, history_key)
        data = json.loads(response.read().decode("utf-8"))
        speaker_centroids = {k: np.array(v, dtype="float32") for k, v in data.get("centroids", {}).items()}
        max_id = data.get("max_id", 0)
    except Exception:
        pass

    # 4. Map Micro-chunks (Tentative)
    next_temp_id = max_id + 1
    micro_results = []
    temp_centroids = speaker_centroids.copy()
    
    for micro in micro_embeddings:
        emb = micro["embedding"]
        best_spk, best_sim = "Unknown", -1
        
        for spk, centroid in temp_centroids.items():
            sim = float(np.dot(emb, centroid))
            if sim > best_sim:
                best_sim, best_spk = sim, spk
        
        # Production Thresholds (Hardened)
        if best_sim > 0.45: # Safer threshold
            assigned_speaker = best_spk
            # Momentum update: only update if it's a good match
            if best_sim > 0.60: # Harder threshold for centroid update
                temp_centroids[assigned_speaker] = (temp_centroids[assigned_speaker] * 0.9) + (emb * 0.1)
                temp_centroids[assigned_speaker] /= (np.linalg.norm(temp_centroids[assigned_speaker]) + 1e-6)
        else:
            assigned_speaker = f"Speaker_{next_temp_id}"
            next_temp_id += 1
            temp_centroids[assigned_speaker] = emb
        
        micro_results.append({"start": micro["start"], "end": micro["end"], "speaker": assigned_speaker})

    # 5. Level 2: Align with Whisper Segments (Majority Vote)
    with open(args.segments_file, "r", encoding="utf-8") as f:
        whisper_segments = json.load(f)

    final_results = []
    used_speakers = set()
    for seg in whisper_segments:
        # TIMESTAMPS: whisper_segments are GLOBAL. We need to offset them to this audio chunk.
        s_start_global = float(seg["start"])
        s_end_global = float(seg["end"])
        
        # Convert to local chunk time
        s_start = s_start_global - args.start_offset
        s_end = s_end_global - args.start_offset
        
        votes = {}
        for micro in micro_results:
            overlap = min(s_end, micro["end"]) - max(s_start, micro["start"])
            if overlap > 0:
                spk = micro["speaker"]
                votes[spk] = votes.get(spk, 0) + overlap
        
        seg_embedding = None
        if votes:
            assigned_speaker = max(votes, key=votes.get)
            # Find the best micro-embedding for this segment
            seg_embedding = get_embedding(audio, s_start, s_end, model)
        else:
            # Direct fallback - forced assignment
            seg_embedding = get_embedding(audio, s_start, s_end, model)
            if isinstance(seg_embedding, np.ndarray):
                best_spk, best_sim = "Unknown", -1
                for spk, centroid in temp_centroids.items():
                    sim = float(np.dot(seg_embedding, centroid))
                    if sim > best_sim:
                        best_sim, best_spk = sim, spk
                
                # If we have any history, pick the best one even if low similarity
                if best_spk != "Unknown":
                    assigned_speaker = best_spk
                else:
                    assigned_speaker = f"Speaker_{next_temp_id}"
                    next_temp_id += 1
                    temp_centroids[assigned_speaker] = seg_embedding
            else:
                assigned_speaker = "Unknown"
        
        final_results.append({
            "chunk": args.chunk_index,
            "startSeconds": s_start_global,
            "endSeconds": s_end_global,
            "speaker": assigned_speaker,
            "text": seg.get("text", ""),
            "embedding": seg_embedding.tolist() if isinstance(seg_embedding, np.ndarray) else None
        })
        if assigned_speaker != "Unknown":
            used_speakers.add(assigned_speaker)

    # 6. Finalize Speaker IDs
    mapping = {}
    
    # 6a. Priority 1: Keep existing global speakers exactly as they are
    for spk in speaker_centroids.keys():
        mapping[spk] = spk
        
    # 6b. Priority 2: Map new speakers found in this chunk to the next available global IDs
    actual_next_id = max_id + 1
    # Sort new speakers to keep them sequential
    new_found = sorted([s for s in used_speakers if s not in mapping], 
                       key=lambda x: int(x.split("_")[1]) if "_" in x else 0)
    
    for spk in new_found:
        new_id = f"Speaker_{actual_next_id}"
        mapping[spk] = new_id
        actual_next_id += 1
            
    # Apply mapping
    final_output = []
    final_centroids = {}
    for r in final_results:
        old_spk = r["speaker"]
        # If it's still Unknown (silence/error), let it be, otherwise map it
        r["speaker"] = mapping.get(old_spk, old_spk)
        final_output.append(r)
        
    for old_spk, centroid in temp_centroids.items():
        if old_spk in mapping:
            final_centroids[mapping[old_spk]] = centroid

    # 7. Persistence
    centroid_payload = {
        "centroids": {spk: emb.tolist() for spk, emb in final_centroids.items()},
        "max_id": actual_next_id - 1
    }
    centroid_bytes = json.dumps(centroid_payload).encode("utf-8")
    client.put_object(args.minio_bucket, f"{prefix}centroids.json",
                     io.BytesIO(centroid_bytes),
                     len(centroid_bytes), content_type="application/json")

    # Save Chunk Results to MinIO instead of returning huge string
    result_path = f"results/{args.job_id}/chunks/diarized_{args.chunk_index}.json"
    result_bytes = json.dumps(final_output).encode("utf-8")
    client.put_object(args.minio_bucket, result_path, io.BytesIO(result_bytes), len(result_bytes), content_type="application/json")

    # Return the KEY, not the CONTENT, to avoid Temporal size limits
    print(result_path)

if __name__ == "__main__":
    main()
