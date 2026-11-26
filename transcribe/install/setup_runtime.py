"""Install isolated, explicitly selected CPU/CUDA runtimes without editing .env."""

import argparse
import csv
import io
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys

INSTALL = Path(__file__).resolve().parent
ROOT = INSTALL.parent
sys.path.insert(0, str(ROOT / "python"))
from runtime_paths import runtime_environment, venv_python
# Full toolkit driver baselines, deliberately stricter than minor compatibility.
# https://docs.nvidia.com/cuda/archive/12.{6,8,9}.0/cuda-toolkit-release-notes/
CUDA_PROFILES = {
    "cu126": ((12, 6), (560, 28, 3), (560, 76, 0)),
    "cu128": ((12, 8), (570, 26, 0), (570, 65, 0)),
    "cu129": ((12, 9), (575, 51, 3), (576, 2, 0)),
}


def version_tuple(value):
    parts = [int(part) for part in value.split(".")]
    return tuple((parts + [0, 0, 0])[:3])


def detect_gpus():
    executable = shutil.which("nvidia-smi")
    if executable is None and Path("/usr/lib/wsl/lib/nvidia-smi").is_file():
        executable = "/usr/lib/wsl/lib/nvidia-smi"
    if executable is None:
        raise ValueError("nvidia-smi is unavailable. Install/enable the NVIDIA driver or use setup_cpu.sh.")
    try:
        header = subprocess.run([executable], capture_output=True, text=True, check=True, timeout=30).stdout
        rows = subprocess.run([executable, "--query-gpu=name,driver_version,compute_cap",
                               "--format=csv,noheader"], capture_output=True, text=True,
                              check=True, timeout=30).stdout
    except (subprocess.SubprocessError, OSError) as error:
        raise ValueError("NVIDIA detection failed. Check the driver, GPU access and nvidia-smi compute_cap support.") from error
    match = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", header)
    if not match:
        raise ValueError("Cannot determine the driver's CUDA capability from nvidia-smi.")
    devices = []
    for row in csv.reader(io.StringIO(rows)):
        if len(row) != 3:
            raise ValueError("Unexpected nvidia-smi GPU query result")
        name, driver, capability = (part.strip() for part in row)
        if not re.fullmatch(r"\d+\.\d+", capability) or not re.fullmatch(r"\d+(?:\.\d+){1,2}", driver):
            raise ValueError("GPU driver or compute capability could not be identified")
        devices.append(dict(name=name, driver=driver, capability=capability))
    if not devices:
        raise ValueError("No NVIDIA GPU found")
    return tuple(map(int, match.groups())), devices


def select_cuda(maximum_cuda, devices, wsl=False, requested="auto"):
    capabilities = [tuple(map(int, gpu["capability"].split("."))) for gpu in devices]
    if not capabilities or min(capabilities) < (7, 5):
        raise ValueError("This installer supports NVIDIA compute capability 7.5+; use CPU for older/unknown GPUs.")
    minimum_cuda = (12, 8) if max(capabilities) >= (10, 0) else (12, 6)
    candidates = []
    for name, (runtime, linux_driver, windows_driver) in CUDA_PROFILES.items():
        driver_minimum = windows_driver if wsl else linux_driver
        if minimum_cuda <= runtime <= maximum_cuda and all(
                version_tuple(gpu["driver"]) >= driver_minimum for gpu in devices):
            candidates.append(name)
    if requested != "auto":
        if requested not in candidates:
            raise ValueError(f"Requested {requested} does not satisfy the detected driver/GPU requirements")
        return requested
    if not candidates:
        raise ValueError("No supported CUDA 12.6/12.8/12.9 build meets the driver/GPU requirements. Update the driver or use CPU.")
    return candidates[-1]


def check_platform(mode, system=None, machine=None):
    system = platform.system() if system is None else system
    machine = platform.machine().lower() if machine is None else machine.lower()
    if system == "Darwin":
        if mode == "gpu":
            raise ValueError("CUDA setup requires NVIDIA on Windows/Linux/WSL2. Use CPU setup on macOS; MPS is not configured.")
        if machine != "arm64":
            raise ValueError("The pinned PyTorch 2.8 speaker runtime needs Apple Silicon on native macOS. Use a Linux VM/container on Intel Macs.")
    elif system not in ("Linux", "Windows") or machine not in ("x86_64", "amd64"):
        raise ValueError("Use Windows/Linux x86-64 or Apple Silicon macOS CPU. Other architectures need separate validation.")


def make_plan(mode, component, profile, asr_dir, speaker_dir):
    suffix = "cpu" if mode == "cpu" else "gpu-" + profile
    plans = []
    if component in ("both", "asr"):
        plans.append(dict(component="asr", environment=str(asr_dir),
                          pytorch=str(INSTALL / f"requirements-pytorch-{suffix}.txt"),
                          application=str(INSTALL / "requirements.txt")))
    if component in ("both", "diarization"):
        plans.append(dict(component="diarization", environment=str(speaker_dir),
                          pytorch=str(INSTALL / f"requirements-diarization-pytorch-{suffix}.txt"),
                          application=str(INSTALL / "requirements-diarization.txt")))
    return plans


def library_environment(directory):
    return runtime_environment(directory)


def verification_code(component, gpu, system=None):
    windows = (platform.system() if system is None else system) == "Windows"
    code = (f"import sys; sys.path.insert(0, {str(ROOT / 'python')!r})\n"
            "from runtime_paths import activate_dll_directories\nactivate_dll_directories(sys.prefix)\n"
            "import torch, torchaudio; print('PyTorch:', torch.__version__, 'CUDA runtime:', torch.version.cuda)\n")
    if gpu:
        code += ("assert torch.cuda.is_available(), 'CUDA is not usable in this environment'\n"
                 "for index in range(torch.cuda.device_count()):\n"
                 " x=torch.ones((16,16), device=f'cuda:{index}'); y=x@x; torch.cuda.synchronize(index)\n"
                 " print('Verified GPU:', torch.cuda.get_device_name(index))\n")
    else:
        code += "assert torch.version.cuda is None, 'Expected a CPU-only PyTorch installation'\n"
    if component == "diarization":
        code += "from pyannote.audio import Pipeline\n"
        if windows:
            # The pipeline supplies waveforms, not file paths. Windows GPU
            # TorchCodec file-decoder builds have different distribution support.
            code += ("from pyannote.audio.core.io import Audio\n"
                     "audio, rate = Audio(sample_rate=16000)({'waveform': torch.zeros(1,1600), 'sample_rate':16000})\n"
                     "assert rate == 16000 and audio.shape == (1,1600)\n"
                     "print('Verified predecoded waveform input; TorchCodec file decoding is not required')\n")
        else:
            code += "from torchcodec.decoders import AudioDecoder\n"
    else:
        code += "import numpy; assert int(numpy.__version__.split('.')[0]) < 2\nfrom faster_whisper import WhisperModel\n"
        if gpu:
            libraries = ("ctypes.WinDLL('cublas64_12.dll'); ctypes.WinDLL('cudnn64_9.dll')" if windows
                         else "ctypes.CDLL('libcublas.so.12'); ctypes.CDLL('libcudnn.so.9')")
            code += ("import ctypes\n" + libraries + "\n"
                     "import ctranslate2\nprint('Whisper CUDA compute types:', ctranslate2.get_supported_compute_types('cuda'))\n")
    return code + "print('Runtime checks passed; model weights are not downloaded by this check')\n"


def run(command, **kwargs):
    print("+ " + shlex.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True, **kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("cpu", "gpu"))
    parser.add_argument("--component", choices=("both", "asr", "diarization"), default="both")
    parser.add_argument("--cuda", choices=("auto", *CUDA_PROFILES), default="auto")
    parser.add_argument("--dry-run", action="store_true", help="Detect hardware and print the plan; install nothing")
    parser.add_argument("--asr-venv", type=Path, default=ROOT / "python/venv")
    parser.add_argument("--diarization-venv", type=Path, default=ROOT / "python/diarization-venv")
    args = parser.parse_args(argv)
    check_platform(args.mode)
    if not (3, 10) <= sys.version_info[:2] <= (3, 12):
        parser.error("Select Python 3.10-3.12 using SETUP_PYTHON; Python 3.12 is preferred")
    if args.mode == "cpu" and args.cuda != "auto":
        parser.error("--cuda is only valid for GPU setup")
    asr_dir, speaker_dir = args.asr_venv.resolve(), args.diarization_venv.resolve()
    if asr_dir == speaker_dir or asr_dir in speaker_dir.parents or speaker_dir in asr_dir.parents:
        parser.error("ASR and speaker environments must be separate, non-nested directories")
    profile, devices, maximum = "cpu", [], None
    if args.mode == "gpu":
        maximum, devices = detect_gpus()
        windows_driver = platform.system() == "Windows" or "microsoft" in platform.release().lower()
        profile = select_cuda(maximum, devices, windows_driver, args.cuda)
    plans = make_plan(args.mode, args.component, profile, asr_dir, speaker_dir)
    print(json.dumps(dict(mode=args.mode, profile=profile, gpus=devices,
                          driver_cuda_capability=maximum, environments=plans), indent=2), flush=True)
    for plan in plans:
        directory = Path(plan["environment"])
        python = venv_python(directory)
        commands = [[sys.executable, "-m", "venv", directory],
                    [python, "-m", "pip", "install", "--no-cache-dir", "--upgrade", "pip"],
                    [python, "-m", "pip", "install", "--no-cache-dir", "--upgrade",
                     "-r", plan["pytorch"], "-r", plan["application"]],
                    [python, "-m", "pip", "check"]]
        if args.dry_run:
            for command in commands:
                print("+ " + shlex.join(map(str, command)))
            print(f"Then verify imports, {'CUDA tensor execution' if args.mode == 'gpu' else 'CPU-only PyTorch'} and {plan['component']} libraries.")
            continue
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise ValueError("Install FFmpeg/FFprobe on PATH before running setup; see the OS instructions in README")
        if python.exists():
            existing = subprocess.check_output([str(python), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"], text=True).strip()
            if existing != f"{sys.version_info.major}.{sys.version_info.minor}":
                raise ValueError(f"{directory} uses Python {existing}; select that interpreter or a new environment directory")
        for command in commands:
            run(command)
        run([python, "-c", verification_code(plan["component"], args.mode == "gpu")],
            env=library_environment(directory))
    print("\n" + ("Dry run complete. " if args.dry_run else "Setup complete. ") + ".env was not edited.")
    print(f"PYTHON_EXECUTABLE={venv_python(asr_dir).as_posix()}\nDIARIZATION_PYTHON={venv_python(speaker_dir).as_posix()}\nDIARIZATION_DEVICE={'cuda' if args.mode == 'gpu' else 'cpu'}")
    print("Stop and restart the worker after exporting the matching .env settings.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.SubprocessError, OSError) as error:
        print(f"Setup failed: {error}", file=sys.stderr)
        sys.exit(1)
