"""Run one bounded speaker process and retain its actual failure details."""

from pathlib import Path
import os
import subprocess
import time
from runtime_paths import runtime_environment


def speaker_environment(python_executable):
    prefix = Path(python_executable).absolute().parent.parent
    return runtime_environment(prefix)


def run_speaker_command(command, log_path, timeout, progress, environment=None):
    if timeout <= 0:
        raise ValueError("Speaker process timeout must be positive")
    log_path = Path(log_path)
    with log_path.open("wb") as log:
        with subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=environment) as process:
            deadline = time.monotonic() + timeout
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    try:
                        code = process.wait(timeout=min(30, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() >= deadline:
                            raise
                        progress()
            except BaseException:
                process.kill()
                process.wait()
                raise
    if code:
        with log_path.open("rb") as log:
            log.seek(0, 2)
            log.seek(max(0, log.tell() - 6000))
            detail = log.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Speaker process exited with code {code}. Log: {log_path}\n{detail}")
