"""Load dotenv data without shell evaluation, resolve tools and start Maven."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def worker_environment(path):
    from dotenv import dotenv_values
    environment = os.environ.copy()
    values = dotenv_values(path, interpolate=False)
    environment.update({key: value for key, value in values.items() if value is not None})
    for key, default in (("FFMPEG_PATH", "ffmpeg"), ("FFPROBE_PATH", "ffprobe")):
        value = environment.get(key, default)
        executable = shutil.which(value, path=environment.get("PATH"))
        if not executable:
            local = Path(value)
            if not local.is_absolute():
                local = ROOT / local
            if not local.is_file():
                raise ValueError(f"{key} does not resolve to an executable; check .env and PATH")
            executable = str(local.resolve())
        environment[key] = str(Path(executable).resolve())
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    path = args.env_file.resolve()
    if not path.is_file():
        raise ValueError("Environment file missing. Copy install/env.windows.example to .env and configure it first.")
    environment = worker_environment(path)
    maven = shutil.which("mvn.cmd", path=environment.get("PATH")) or shutil.which("mvn", path=environment.get("PATH"))
    if not maven:
        raise ValueError("Maven not found on PATH; install Maven and reopen PowerShell")
    print(f"Starting worker from {ROOT} using {path.name}; configuration values are not printed", flush=True)
    return subprocess.call([maven, "spring-boot:run"], cwd=ROOT, env=environment)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(f"Cannot start worker: {error}", file=sys.stderr)
        raise SystemExit(1)
