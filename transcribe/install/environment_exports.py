"""Machine-readable dotenv exports consumed by the terminal loader scripts."""

import argparse
import json
from pathlib import Path
import re
import shlex
import sys

from dotenv import dotenv_values
from run_worker import worker_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--format", choices=("bash", "json"), required=True)
    args = parser.parse_args()
    if not args.env_file.is_file():
        raise ValueError("Environment file not found; create .env from the appropriate example first")
    values = dotenv_values(args.env_file, interpolate=False)
    keys = {key for key, value in values.items() if value is not None}
    keys.update(("FFMPEG_PATH", "FFPROBE_PATH", "PYTHONUTF8", "PYTHONIOENCODING"))
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) for key in keys):
        raise ValueError("Environment file contains an invalid variable name")
    environment = worker_environment(args.env_file)
    exports = {key: environment[key] for key in sorted(keys)}
    if args.format == "json":
        print(json.dumps(exports))
    else:
        # Only validated names and shell-quoted values enter the generated code.
        # Dotenv contents such as $() remain literal data, never executable code.
        print("\n".join(f"export {key}={shlex.quote(value)}" for key, value in exports.items()))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"Cannot load environment: {error}", file=sys.stderr)
        raise SystemExit(1)
