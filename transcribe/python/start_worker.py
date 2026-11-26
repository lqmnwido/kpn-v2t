"""Expose the selected venv's CUDA libraries before importing the ASR worker."""

import os
from pathlib import Path
import sys
import platform
import runpy

from speaker_subprocess import speaker_environment
from runtime_paths import activate_dll_directories, venv_python


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: start_worker.py /path/to/persistent_worker.py")
    script = Path(sys.argv[1]).resolve(strict=True)
    environment = speaker_environment(venv_python(sys.prefix))
    if platform.system() == "Windows":
        # Windows exec spawns another process; keep the Java-owned process and
        # pipe lifetime instead, and register Python 3.8+ DLL search directories.
        os.environ.update(environment)
        activate_dll_directories(sys.prefix)
        sys.argv = [str(script)]
        sys.path.insert(0, str(script.parent))
        runpy.run_path(str(script), run_name="__main__")
    else:
        os.execve(sys.executable, [sys.executable, "-u", str(script)], environment)
