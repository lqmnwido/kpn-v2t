import os
import sys
import json
import argparse
import numpy as np
from minio import Minio
import io
from transcript_acceptance import apply_acceptance

def format_timestamp(seconds):
    td = float(seconds)
    hours = int(td // 3600)
    minutes = int((td % 3600) // 60)
    secs = int(td % 60)
    millis = int((td % 1) * 1000)
    return f"[{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}]"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--minio-url", required=True)
    parser.add_argument("--minio-access-key", required=True)
    parser.add_argument("--minio-secret-key", required=True)
    parser.add_argument("--minio-bucket", required=True)
    parser.add_argument("--transcript-key", required=True)
    args = parser.parse_args()

    client = Minio(
        args.minio_url.replace("http://", "").replace("https://", ""),
        access_key=args.minio_access_key,
        secret_key=args.minio_secret_key,
        secure=args.minio_url.startswith("https"),
    )

    # 1. Download
    response = client.get_object(args.minio_bucket, args.transcript_key)
    data = json.loads(response.read().decode("utf-8"))
    segments = [apply_acceptance(segment) for segment in data.get("segments", [])]

    if not segments:
        print(args.transcript_key)
        return

    prepared = all(s.get("diarization_backend") == "pyannote-community-1" for s in segments)
    if not prepared:
        # 2. Extract and Normalize
        valid_indices = []
        embeddings = []
        for i, seg in enumerate(segments):
            emb = seg.get("embedding")
            if emb and isinstance(emb, list) and len(emb) == 192:
                e = np.array(emb)
                e = e / (np.linalg.norm(e) + 1e-6)
                embeddings.append(e)
                valid_indices.append(i)

        if len(embeddings) < 2:
            # No evidence that all turns belong to one person.
            for seg in segments:
                seg["speaker"] = "Unknown"
            if valid_indices:
                segments[valid_indices[0]]["speaker"] = "Speaker_1"
        else:
            embeddings = np.array(embeddings)
            from sklearn.cluster import AgglomerativeClustering
            from sklearn.metrics.pairwise import cosine_similarity

            # 3. Initial Clustering (Stable Base)
            # Threshold 0.6 is more "mergy" for ECAPA, helping group the same voice
            clusterer = AgglomerativeClustering(
                n_clusters=None,
                distance_threshold=0.6,
                metric='cosine',
                linkage='average'
            )
            labels = clusterer.fit_predict(embeddings)

            # 4. SOTA Refinement: Centroid Re-assignment
            # This fixes "flickering" and drift in short segments
            centroids = {}
            for idx, label in enumerate(labels):
                if label not in centroids: centroids[label] = []
                centroids[label].append(embeddings[idx])

            for label in centroids:
                centroids[label] = np.mean(centroids[label], axis=0)
                centroids[label] /= (np.linalg.norm(centroids[label]) + 1e-6)

            # Re-assign based on nearest centroid
            refined_labels = []
            centroid_keys = list(centroids.keys())
            centroid_matrix = np.array([centroids[k] for k in centroid_keys])

            similarities = cosine_similarity(embeddings, centroid_matrix)
            refined_labels = [centroid_keys[i] for i in np.argmax(similarities, axis=1)]

            # 5. Sequential Speaker IDs (Appearance Order)
            label_map = {}
            next_id = 1
            for label in refined_labels:
                if label not in label_map:
                    label_map[label] = f"Speaker_{next_id}"
                    next_id += 1

            # Gallery IDs and global cluster IDs are different namespaces. An
            # unembedded turn cannot safely inherit either cluster's identity.
            for seg in segments:
                seg["speaker"] = "Unknown"
            for i, idx in enumerate(valid_indices):
                segments[idx]["speaker"] = label_map[refined_labels[i]]

            # Keep brief speakers: speaking duration is not identity evidence.

    # 7. Final Formatting
    for seg in segments:
        if "embedding" in seg: del seg["embedding"]

    data["segments"] = segments
    data["text"] = "\n".join([f"{s.get('speaker', 'Speaker_1')}: {s['text']}" for s in segments])

    # 8. Save
    refined_json = json.dumps(data, indent=2).encode("utf-8")
    client.put_object(args.minio_bucket, args.transcript_key, io.BytesIO(refined_json), len(refined_json), content_type="application/json")

    unique_speakers = sorted(list(set(s.get("speaker", "Speaker_1") for s in segments)))
    speaker_map_data = {"jobId": args.job_id, "speakers": unique_speakers}
    client.put_object(args.minio_bucket, f"results/{args.job_id}/speaker_map.json",
                     io.BytesIO(json.dumps(speaker_map_data).encode("utf-8")),
                     len(json.dumps(speaker_map_data)), content_type="application/json")

    print(args.transcript_key)

if __name__ == "__main__":
    main()
