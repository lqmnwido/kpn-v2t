"""Platform-specific executable and library paths, without importing ML packages."""

import os
from pathlib import Path
import platform
import shutil

_DLL_HANDLES = []


def venv_python(prefix, system=None):
    system = platform.system() if system is None else system
    return Path(prefix) / ("Scripts/python.exe" if system == "Windows" else "bin/python")


def library_directories(prefix, system=None):
    system = platform.system() if system is None else system
    prefix = Path(prefix)
    if system == "Windows":
        paths = [prefix / "Lib/site-packages/torch/lib"]
        for suffix in ("bin", "lib"):
            paths += sorted(prefix.glob(f"Lib/site-packages/nvidia/*/{suffix}"))
        executable = shutil.which("ffmpeg")
        if executable:
            paths.append(Path(executable).absolute().parent)
    else:
        paths = sorted(prefix.glob("lib/python*/site-packages/nvidia/*/lib"))
        paths += sorted(prefix.glob("lib/python*/site-packages/torch/lib"))
    return list(dict.fromkeys(path for path in paths if path.is_dir() and path.parent.name != "cu13"))


def runtime_environment(prefix, system=None):
    system = platform.system() if system is None else system
    environment = os.environ.copy()
    paths = library_directories(prefix, system)
    if paths:
        key, separator = ("PATH", ";") if system == "Windows" else ("LD_LIBRARY_PATH", ":")
        previous = environment.get(key, "")
        environment[key] = separator.join(map(str, paths)) + (separator + previous if previous else "")
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def activate_dll_directories(prefix):
    # Handles must remain alive while native extension modules use their DLLs.
    if platform.system() == "Windows" and hasattr(os, "add_dll_directory"):
        for directory in library_directories(prefix, "Windows"):
            _DLL_HANDLES.append(os.add_dll_directory(str(directory.absolute())))
