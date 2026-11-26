"""Read public speech with matching process metadata for internal analysis."""

from copy import deepcopy
import json
from minio.error import S3Error


def read_json(client, bucket, key):
    response = client.get_object(bucket, key)
    try:
        return json.loads(response.read().decode("utf-8"))
    finally:
        response.close()
        response.release_conn()


def source_key(segment):
    return (segment.get("start", segment.get("startSeconds")),
            segment.get("end", segment.get("endSeconds")),
            segment.get("speaker", "Unknown"), segment.get("text", "").strip())


def load_transcript(client, bucket, key):
    data = read_json(client, bucket, key)
    if not key.endswith("/transcript.json"):
        return data
    try:
        process = read_json(client, bucket, key.removesuffix(".json") + ".process.json")
    except S3Error as error:
        if error.code != "NoSuchKey":
            raise
        process = {}
    # Public speech remains authoritative, including manual corrections. Never
    # reinsert withheld rows or stale process text into analysis input.
    metadata = {}
    labels = process.get("publishedSpeakerLabels", {})
    for original in process.get("segments", []):
        candidate = deepcopy(original)
        source = candidate.get("speaker", "Unknown")
        if source in labels:
            candidate["sourceSpeaker"] = source
            candidate["speaker"] = labels[source]
        metadata[source_key(candidate)] = candidate
    segments = []
    for index, public in enumerate(data.get("segments", []), 1):
        segment = deepcopy(metadata.get(source_key(public), public))
        segment.setdefault("segmentId", f"seg_{index:06d}")
        segment.setdefault("asr_quality", {"review_required": True, "flags": []})
        segments.append(segment)
    data["segments"] = segments
    return data
