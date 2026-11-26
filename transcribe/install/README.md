# Runtime installers

Run from the repository root after installing Python 3.10-3.12 (prefer 3.12),
FFmpeg and FFprobe. Java 21 and Maven are also needed to run the application.
Stop the worker before updating an environment that it currently uses.

## Linux and Windows WSL2 (Bash)

Preview without creating environments or downloading packages:

```bash
bash install/setup_cpu.sh --dry-run
bash install/setup_gpu.sh --dry-run
```

Install both ASR and speaker runtimes, choosing one mode:

```bash
bash install/setup_cpu.sh
```

```bash
bash install/setup_gpu.sh
```

Native Windows PowerShell uses the `.ps1` commands below.

GPU setup queries `nvidia-smi` for every GPU's name, driver and compute capability,
and the driver's advertised maximum CUDA version. It chooses the newest
supported profile compatible with all detected devices: `cu126`, `cu128` or
`cu129`. It does not use `nvcc` or install/change the system NVIDIA driver.
CUDA 13-capable drivers can run the selected CUDA 12 wheels; the installer does
not select CUDA 13 for the current Faster-Whisper stack.

Selection uses conservative full-toolkit driver baselines:

| Profile | Linux minimum driver | WSL Windows minimum driver |
| --- | --- | --- |
| CUDA 12.6 | 560.28.03 | 560.76 |
| CUDA 12.8 | 570.26 | 570.65 |
| CUDA 12.9 | 575.51.03 | 576.02 |

This is deliberately stricter than CUDA minor-version compatibility. The
installer supports NVIDIA compute capability 7.5 and newer; GPUs with capability
10.0 or newer require at least the CUDA 12.8 profile. Unsupported drivers,
unreadable GPU information or older architectures fail before installation;
there is no silent CPU fallback. GPU execution is checked after installation.

You can select an older supported profile explicitly; it must still pass the
same driver/hardware checks:

```bash
bash install/setup_gpu.sh --cuda cu126
```

Sources: [PyTorch 2.8 wheel profiles](https://pytorch.org/get-started/previous-versions/),
[CUDA 12.6 drivers](https://docs.nvidia.com/cuda/archive/12.6.0/cuda-toolkit-release-notes/index.html),
[CUDA 12.8 drivers](https://docs.nvidia.com/cuda/archive/12.8.0/cuda-toolkit-release-notes/index.html),
[CUDA 12.9 drivers](https://docs.nvidia.com/cuda/archive/12.9.0/cuda-toolkit-release-notes/index.html),
and [Faster-Whisper GPU requirements](https://github.com/SYSTRAN/faster-whisper#gpu).

## macOS

On Apple Silicon, install prerequisites with Homebrew and use the CPU installer:

```bash
brew install python@3.12 ffmpeg libsndfile
bash install/setup_cpu.sh --dry-run
bash install/setup_cpu.sh
```

It selects native macOS PyTorch wheels and CPU execution. NVIDIA CUDA and Apple
MPS are not enabled by this installer. Intel Macs need the Linux container or VM
route because the pinned PyTorch 2.8 speaker runtime has no native Intel Mac wheel.
See the main [README](../README.md#operating-system-tutorials) for Java, Maven,
service endpoints and the container tutorial.

## Native Windows PowerShell

The `.ps1` entry points install **native Windows environments by default**.
They do not need WSL or Docker. Install 64-bit Python 3.12, FFmpeg/FFprobe, Java
21, Maven, Git and the Microsoft Visual C++ x64 runtime first, and reopen
PowerShell after updating PATH. See the main [native Windows tutorial](../README.md#windows-native-powershell-without-docker-or-wsl).

From the repository root, choose one mode:

```powershell
.\install\setup_cpu.ps1 -DryRun
.\install\setup_cpu.ps1
```

```powershell
nvidia-smi
.\install\setup_gpu.ps1 -DryRun
.\install\setup_gpu.ps1
```

The helper first tries the Windows Python launcher for versions 3.12, 3.11 and
3.10, then compatible Python executables on PATH. You can specify the interpreter:

```powershell
.\install\setup_gpu.ps1 -PythonExecutable 'C:/Path To/Python/python.exe' -Cuda cu128 -Component diarization
```

It creates `python/venv/Scripts/python.exe` and
`python/diarization-venv/Scripts/python.exe`, uses Windows NVIDIA driver minimums,
and registers DLL directories for import/runtime checks. Windows GPU builds use
the CUDA/cuDNN DLLs bundled in PyTorch; the Linux-only NVIDIA pip packages are
excluded by requirements markers.

Create and edit the Windows configuration, preserving an existing `.env`:

```powershell
if (-not (Test-Path .env)) { Copy-Item install/env.windows.example .env }
notepad .env
.\install\load_env.ps1
mvn spring-boot:run
```

For GPU setup change `DIARIZATION_DEVICE` to `cuda` before starting. The env-only
loader reads `.env` as data and resolves FFmpeg/FFprobe through PATH in the
current PowerShell process. Then run Maven yourself. It expects the default
main environment unless you pass `-PythonExecutable` for a custom one. The
optional `start_worker.ps1` combines loading and Maven startup.
Relative `ASR_WORK_DIR=./work` is resolved to an absolute working directory by
the worker. The generated `work/` directory is ignored by Git.

If PowerShell execution policy blocks scripts, use a process-scoped invocation:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install\setup_gpu.ps1 -DryRun
```

To allow the env-only loader in the current session if policy blocks it, use
`Set-ExecutionPolicy -Scope Process Bypass` there. Do not launch `load_env.ps1`
in a separate PowerShell process: its environment changes would not reach the
terminal where you subsequently start Maven.

The worker supplies predecoded waveform tensors to pyannote, using SoundFile to
read normalized WAVs. Native Windows checks verify this input route. They do
not require TorchCodec's separate file decoder to work on GPU; an upstream
warning about that optional decoder does not mean waveform inference is broken.
See [TorchCodec 0.7 Windows packaging](https://github.com/meta-pytorch/torchcodec/releases/tag/v0.7.0).
Other applications that ask TorchCodec to open audio/video files need their own
working decoder installation. No model weights are fetched by these checks.

### Optional WSL route

WSL remains available explicitly through `-UseWSL`. First install and configure
Ubuntu with `wsl --install -d Ubuntu-24.04`, then its Linux prerequisites. From a
Windows checkout accessible to the distribution:

```powershell
.\install\setup_cpu.ps1 -UseWSL -DryRun
.\install\setup_gpu.ps1 -UseWSL -Distribution Ubuntu-24.04
```

That mode creates Linux `bin/python` environments, and the worker must run in
WSL afterward. Do not share virtual environments across native Windows and WSL.
For a checkout in `~/Projects` inside Ubuntu, run the Bash entry points there
instead. Only WSL mode uses `wslpath` to translate the checkout location.

## Components and environment locations

### Starting the worker after installation

Run Maven from the repository root on Linux, macOS, WSL, or native Windows.

```sh
mvn spring-boot:run
```

Spring Boot reads `.env` directly. Use plain `KEY=value` lines and restart
Maven after editing the file.

Optional overrides:

```bash
source install/load_env.sh --env-file /path/to/worker.env --python-executable /path/to/venv/bin/python
```

The existing `start_worker.ps1` remains an optional Windows shortcut that loads
settings and starts Maven together. In production, configure the service manager
to supply environment variables to `java -jar`; loading settings in an interactive
terminal does not update a separately running production service.

Both components are installed by default. To update only one:

```bash
bash install/setup_cpu.sh --component asr
bash install/setup_gpu.sh --component diarization
```

The default directories remain `python/venv` for ASR and `python/diarization-venv`
for speakers. Executables use `Scripts/python.exe` on Windows and `bin/python`
on Linux/macOS. They are separate because their NumPy requirements differ. To use
other directories or an explicit interpreter:

```bash
SETUP_PYTHON=/usr/bin/python3.12 bash install/setup_gpu.sh \
  --asr-venv /path/to/asr-venv --diarization-venv /path/to/speaker-venv
```

The installer can update matching existing environments. It refuses an existing
environment created with a different Python minor version: select that interpreter
or a new directory. It does not delete environments or modify `.env`. If pip or
verification fails, the command exits with an error; package updates already
performed are not rolled back automatically.

## Requirements files and manual pip installation

The scripts install the selected PyTorch profile and application requirements in
one pip operation per environment, preventing application dependencies from
silently replacing the CPU/CUDA selection.

| File | Purpose |
| --- | --- |
| `requirements.txt` | Includes the main ASR dependencies from `python/requirements.txt`. |
| `requirements-diarization.txt` | Includes the speaker dependencies from `python/requirements-diarization.txt`. |
| `requirements-pytorch-cpu.txt` | Explicit PyTorch/TorchAudio 2.8 CPU packages; macOS ARM64 uses native wheels. |
| `requirements-pytorch-gpu.txt` | Manual default alias for CUDA 12.6. Does not detect hardware. |
| `requirements-pytorch-gpu-cu126.txt`, `-cu128.txt`, `-cu129.txt` | Matching GPU PyTorch/TorchAudio and CUDA 12/cuDNN 9 libraries. |
| `requirements-diarization-pytorch-cpu.txt` | CPU PyTorch profile plus matching TorchCodec 0.7. |
| `requirements-diarization-pytorch-gpu.txt` | Manual speaker alias for CUDA 12.6. |
| `requirements-diarization-pytorch-gpu-cu126.txt`, `-cu128.txt`, `-cu129.txt` | GPU profiles plus matching TorchCodec 0.7. |

For requirements-only installation, first create the separate environments, then
select the appropriate files. This CPU example supports Linux/WSL2 and Apple
Silicon macOS:

```bash
python3.12 -m venv python/venv
python3.12 -m venv python/diarization-venv
python/venv/bin/python -m pip install \
  -r install/requirements-pytorch-cpu.txt -r install/requirements.txt
python/diarization-venv/bin/python -m pip install \
  -r install/requirements-diarization-pytorch-cpu.txt -r install/requirements-diarization.txt
```

For GPU, run the GPU dry-run first to identify the profile; substitute its matching
GPU files in those pip commands. The manual commands do not run the installer's
post-install checks and do not automatically detect a compatible CUDA version.

The equivalent manual CPU installation in native Windows PowerShell is:

```powershell
py -3.12 -m venv python/venv
py -3.12 -m venv python/diarization-venv
.\python\venv\Scripts\python.exe -m pip install -r install/requirements-pytorch-cpu.txt -r install/requirements.txt
.\python\diarization-venv\Scripts\python.exe -m pip install -r install/requirements-diarization-pytorch-cpu.txt -r install/requirements-diarization.txt
```

## Checks and `.env` after installation

The installer checks package dependencies, component imports and, in GPU mode,
actual PyTorch matrix execution on each visible device plus ASR CUDA library
loading. Native Windows speaker checks use the predecoded waveform route;
other platforms additionally check TorchCodec decoder imports. It does not download transcription/speaker model weights or benchmark
recognition accuracy. Those still need the short-recording check in the main README.

### Hugging Face token and model downloads

`HF_TOKEN` is **not required to install packages** with these scripts. Import
verification does not call `Pipeline.from_pretrained`, so it does not download
pyannote model weights. The first speaker-detection job loads Community-1 and
downloads any weights that are missing from the selected runtime user's cache.
Before that job, accept the Community-1 access conditions and set your own
`HF_TOKEN` in the worker's `.env`.

Once all required model files are cached, the token is not needed for computation
itself. Do not assume a single completed download makes every future load offline:
model configuration, submodels or cache changes may still require Hub access.
You can check cache-only loading without supplying an explicit or implicitly
saved token, from Linux/WSL or macOS Bash:

```bash
env -u HF_TOKEN HF_HUB_OFFLINE=1 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
  python/diarization-venv/bin/python -c \
  'import os; from pyannote.audio import Pipeline; Pipeline.from_pretrained(os.environ.get("DIARIZATION_MODEL", "pyannote/speaker-diarization-community-1")); print("Offline model loading OK")'
```

This check actually loads cached model weights and can take time, but cannot
download missing Hub files in offline mode. A successful check establishes local
loading for that user/cache/model configuration, not another machine or a future
model version. Before removing `HF_TOKEN`, verify an actual short job in the
same offline configuration. For offline deployment, retain the complete cache
and export `HF_HUB_OFFLINE=1`, or use a complete tested local model directory
through `DIARIZATION_MODEL`. Removing `HF_TOKEN` from `.env` also requires clearing
any already-exported token and restarting the worker. A fresh container/user or
cleared cache needs the files transferred or authenticated downloads again.

Set these values in the worker's `.env` on Linux/macOS (native Windows uses
`install/env.windows.example`; use the printed paths for custom locations):

```dotenv
PYTHON_EXECUTABLE=python/venv/bin/python
DIARIZATION_PYTHON=python/diarization-venv/bin/python
DIARIZATION_DEVICE=cpu
```

For GPU setup, use `DIARIZATION_DEVICE=cuda` instead. Supply your own `HF_TOKEN`
and configure Temporal, MinIO and Ollama as described in the main README. Export
`.env` and restart the worker. The legacy `python/setup_diarization.sh` still
exists for old deployments; the new installers do not require it.

## Verification limits

Installer selection, Windows paths, DLL handling, dotenv loading and PowerShell
syntax/argument routing have automated checks from the Linux development host.
Native Windows full installation and a real Windows transcription job have not
been executed here. Run the checks and a short recording on your Windows host;
package installation or import failures are reported rather than bypassed.
