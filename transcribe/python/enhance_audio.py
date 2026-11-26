"""Bounded, non-generative speech cleanup with measured loudness and audit data."""

import argparse
import json
import math
from pathlib import Path
import subprocess
import tempfile


LEGACY_FILTER = "loudnorm=I=-23:TP=-2:LRA=11"
# Conservative stationary-noise suppression; no gate or silence removal.
CLEANUP = "highpass=f=60,afftdn=nr=6:nf=-50:gs=6"
LISTENING = CLEANUP + ",equalizer=f=2500:t=q:w=1:g=2,acompressor=threshold=0.125:ratio=2:attack=20:release=250:makeup=1.5"


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    if result.returncode:
        raise RuntimeError(f"Audio command failed ({result.returncode}): {result.stderr[-4000:]}")
    return result


def probe(path, ffprobe):
    result = run([ffprobe, "-v", "error", "-select_streams", "a:0",
                  "-show_entries", "stream=sample_rate,channels,duration:format=duration",
                  "-of", "json", str(path)])
    data = json.loads(result.stdout)
    if not data.get("streams"):
        raise ValueError("Input has no audio stream")
    stream = data["streams"][0]
    duration = float(stream.get("duration", data["format"].get("duration")))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Audio duration must be finite and positive")
    return dict(sample_rate=stream["sample_rate"], channels=stream["channels"], duration=duration)


def measured_filter(input_path, base, target, ffmpeg):
    # Downmix identically in both passes, before measurement and filtering.
    prefix = "aformat=channel_layouts=mono," + base
    result = run([ffmpeg, "-hide_banner", "-nostdin", "-nostats", "-filter_threads", "1", "-threads", "1", "-i", str(input_path),
                  "-map", "0:a:0", "-vn", "-af",
                  prefix + f",loudnorm=I={target}:TP=-2:LRA=9:print_format=json",
                  "-f", "null", "-"])
    start, end = result.stderr.rfind("{"), result.stderr.rfind("}")
    if start < 0 or end < start:
        raise ValueError("FFmpeg did not return loudness measurements")
    measurement = json.loads(result.stderr[start:end + 1])
    fields = dict(measured_I="input_i", measured_TP="input_tp",
                  measured_LRA="input_lra", measured_thresh="input_thresh", offset="target_offset")
    finite = all(math.isfinite(float(measurement[key])) for key in fields.values())
    loudnorm = f"loudnorm=I={target}:TP=-2:LRA=9"
    if finite:
        loudnorm += ":" + ":".join(f"{name}={measurement[key]}" for name, key in fields.items())
        loudnorm += ":linear=true"
    # Digital silence has -inf loudness: never feed non-finite values to FFmpeg.
    return prefix + "," + loudnorm, measurement, finite


def decoded_duration(input_path, ffmpeg):
    """Measure decoded samples; raw AAC header durations can be bitrate estimates."""
    rate = 16000
    # Spool PCM to disk rather than retaining long recordings in memory.
    with tempfile.TemporaryFile() as pcm:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", str(input_path),
             "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(rate),
             "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"],
            stdout=pcm, stderr=subprocess.PIPE, text=True, timeout=1800)
        if result.returncode:
            raise RuntimeError(f"Audio decode failed ({result.returncode}): {result.stderr[-4000:]}")
        duration = pcm.tell() / (rate * 2)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Decoded audio duration must be finite and positive")
    return duration


def enhance(input_path, output_dir, ffmpeg="ffmpeg", ffprobe="ffprobe", audio_filter="auto"):
    input_path, output_dir = Path(input_path), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized = output_dir / "audio-normalized.wav"
    listening = output_dir / "audio-enhanced.m4a"
    if input_path.resolve() in (normalized.resolve(), listening.resolve()):
        raise ValueError("Enhancement must not overwrite its input")
    source = probe(input_path, ffprobe)
    source["reported_duration"] = source["duration"]
    source["duration"] = decoded_duration(input_path, ffmpeg)
    automatic = audio_filter in ("auto", LEGACY_FILTER)
    report = dict(policy="speech-cleanup-v1", source=source,
                  original_preserved=True, automatic_profile=automatic, outputs={})
    for name, path, base, target, rate, codec in (
        ("asr", normalized, CLEANUP, -23, 16000, ["-c:a", "pcm_s16le"]),
        ("listening", listening, LISTENING, -18, 48000, ["-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart"]),
    ):
        if name == "asr" and not automatic:
            filters, measurement, two_pass = audio_filter, None, False
        else:
            filters, measurement, two_pass = measured_filter(input_path, base, target, ffmpeg)
        run([ffmpeg, "-hide_banner", "-nostdin", "-nostats", "-filter_threads", "1", "-threads", "1", "-v", "error", "-y",
             "-i", str(input_path), "-map", "0:a:0", "-vn", "-af", filters,
             "-ac", "1", "-ar", str(rate), *codec, str(path)])
        info = probe(path, ffprobe)
        if abs(info["duration"] - source["duration"]) > .1:
            raise ValueError(
                f"{name} enhancement changed duration (decoded source={source['duration']:.6f}s, "
                f"output={info['duration']:.6f}s); refusing shifted transcript timings")
        if int(info["channels"]) != 1 or int(info["sample_rate"]) != rate:
            raise ValueError(f"Unexpected {name} output format")
        report["outputs"][name] = dict(file=path.name, filters=filters,
                                       measured_two_pass=two_pass, measurements=measurement, **info)
    report_path = output_dir / "audio-enhancement.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--audio-filter", default="auto")
    args = parser.parse_args()
    report = enhance(args.input, args.output_dir, args.ffmpeg, args.ffprobe, args.audio_filter)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
