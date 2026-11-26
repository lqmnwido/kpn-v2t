# ASR Tools

ASR Tools is a Spring Boot worker that transcribes recordings with Faster-Whisper,
labels speakers with pyannote, and stores results in MinIO. Temporal coordinates
the job. Optional Ollama proofreading checks proposed edits against the audio.

For a CPU setup on Linux, macOS (Apple Silicon), or WSL2, install Java 21, Maven,
Python 3.12, and FFmpeg with FFprobe. Have Temporal and a MinIO bucket available,
then run these commands from a Bash terminal.

```bash
git clone https://github.com/lqmnwido/asr-tools.git
cd asr-tools
bash install/setup_cpu.sh
cp -n .env.example .env
```

Set `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` and the service endpoints in your local
`.env`. Set `HF_TOKEN` if downloading gated pyannote weights. Keep real credentials
in the ignored `.env` file or inject them through your deployment environment.

```bash
mvn spring-boot:run
```

See the [operating system tutorials](#operating-system-tutorials) for native
Windows, NVIDIA GPU setup, and containers.

## Python virtual environments

The setup scripts create two virtual environments under `python/`. A
repository-root `.venv` is unnecessary.

| Purpose | `.env` setting | Linux, macOS, and WSL2 | Native Windows |
| --- | --- | --- | --- |
| Transcription | `PYTHON_EXECUTABLE` | `python/venv/bin/python` | `python/venv/Scripts/python.exe` |
| Speaker diarization | `DIARIZATION_PYTHON` | `python/diarization-venv/bin/python` | `python/diarization-venv/Scripts/python.exe` |

Run the setup for your host if either interpreter is missing. If both
environments are already installed and working, reuse them and rebuild and
restart the Java worker to apply code changes. For editor setup, select the
transcription interpreter. Start the worker from the repository root when
using these relative paths.

## Operating system tutorials

Choose the instructions for your host, then follow the shared `.env` setup below.
Linux/macOS use Bash and `bin/python`; native Windows uses PowerShell and
`Scripts/python.exe`. Windows users can choose native installation or WSL2.
These tutorials do not establish that every OS/CPU combination has been tested.

The speaker dependency architecture distinction follows the
[PyTorch 2.8 wheel listings](https://download.pytorch.org/whl/cpu/torch/), which
provide macOS ARM64 wheels for that release. Homebrew package names are listed
in its [Python 3.12](https://formulae.brew.sh/formula/python@3.12),
[Java 21](https://formulae.brew.sh/formula/openjdk@21) and
[FFmpeg](https://formulae.brew.sh/formula/ffmpeg) documentation.

| Host | Installation route | GPU behavior |
| --- | --- | --- |
| Ubuntu 24.04 / compatible Linux | Native Linux tutorial | CPU initially; NVIDIA CUDA requires compatible drivers and packages. |
| Windows 10/11 x64 | Native PowerShell tutorial; WSL2 is optional | CPU or compatible NVIDIA CUDA; both Python environments are checked. |
| macOS on Apple Silicon | Homebrew and native CPU environments, or Linux container | This worker selects CPU on Mac; Apple MPS is not selected by its ASR code. |
| macOS on Intel | Linux x86-64 container or Ubuntu VM | Use CPU; the pinned speaker runtime has no PyTorch 2.8 macOS Intel wheel. |
| Other Linux distributions | Install equivalent dependencies or use the Linux container route | Package names and Python availability vary. |

### Linux: Ubuntu 24.04 and compatible systems

Ubuntu 24.04 provides Python 3.12. Run in Bash:

```bash
sudo apt-get update
sudo apt-get install -y git openjdk-21-jdk maven ffmpeg python3.12 python3.12-venv libsndfile1
git clone https://github.com/lqmnwido/asr-tools.git
cd asr-tools
java -version
mvn -version
python3.12 --version
ffmpeg -version
ffprobe -version
```

If already cloned, enter that checkout. On Pop!_OS or another Ubuntu release, check
`python3.12 --version` first: these exact package commands assume the packages
exist in that release's repositories. Otherwise use an installed Python 3.12
interpreter or the container route. Do not replace the OS-managed Python.

Continue with **Setup and loading `.env`**. The example `/usr/bin/ffmpeg` and
`/usr/bin/ffprobe` paths normally match this package installation; verify with
`command -v ffmpeg` and `command -v ffprobe`.

### Windows: native PowerShell, without Docker or WSL

Install **64-bit Python 3.12**, **Java 21 JDK**, **Maven**, **Git**, **FFmpeg with
FFprobe**, and the **Microsoft Visual C++ 2015-2022 x64 Redistributable**. Use the
official [Python downloads](https://www.python.org/downloads/windows/),
[Java builds](https://adoptium.net/temurin/releases/?version=21),
[Maven installation guide](https://maven.apache.org/install.html),
[Git for Windows](https://git-scm.com/downloads/win),
[FFmpeg Windows downloads](https://ffmpeg.org/download.html#build-windows), and
[Microsoft C++ runtime](https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist).
Add the installed executable directories to PATH and reopen PowerShell.
These system prerequisites are not installed by the Python setup scripts.

Verify the tools from PowerShell:

```powershell
py -3.12 --version
java -version
mvn -version
git --version
ffmpeg -version
ffprobe -version
```

`mvn -version` must report Java 21. Set `JAVA_HOME` to the Java 21 JDK directory
if another installed Java is selected. Then clone and enter the checkout:

```powershell
git clone https://github.com/lqmnwido/asr-tools.git
Set-Location asr-tools
```

Choose **one** mode. CPU setup:

```powershell
.\install\setup_cpu.ps1 -DryRun
.\install\setup_cpu.ps1
```

Or NVIDIA GPU setup:

```powershell
nvidia-smi
.\install\setup_gpu.ps1 -DryRun
.\install\setup_gpu.ps1
```

PowerShell installers now run natively by default. They select Python 3.12,
3.11 or 3.10, create separate Windows environments, install matching requirements
and verify the runtime. GPU detection uses Windows driver thresholds. An explicit
interpreter can be passed with `-PythonExecutable 'C:/Path To/Python/python.exe'`.
Do not share a virtual environment created by Linux/WSL with native Windows.

Create `.env` without overwriting an existing configuration:

```powershell
if (-not (Test-Path .env)) { Copy-Item install/env.windows.example .env }
notepad .env
```

Configure your actual Temporal, MinIO and Ollama endpoints/credentials and
`HF_TOKEN`, and check these native Windows values:

```dotenv
PYTHON_EXECUTABLE=python/venv/Scripts/python.exe
DIARIZATION_PYTHON=python/diarization-venv/Scripts/python.exe
FFMPEG_PATH=ffmpeg.exe
FFPROBE_PATH=ffprobe.exe
ASR_WORK_DIR=./work
DIARIZATION_DEVICE=cpu
```

Use `DIARIZATION_DEVICE=cuda` after successful GPU setup. Forward slashes work in
these Python/Java paths and avoid backslash escapes in quoted dotenv values.
`work/` is ignored by Git. For an existing Linux `.env`, merge these native path
changes while preserving service credentials; copying the Windows template is
not required if the file already exists.

Start Maven from the repository root in PowerShell:

```powershell
mvn spring-boot:run
```

Spring Boot reads `.env` at startup. Keep FFmpeg and FFprobe on PATH or use
absolute paths in `.env`. Restart Maven after editing the file. The
`load_env.ps1` loader and `start_worker.ps1` launcher also support custom
environment locations.

If execution policy blocks scripts, invoke only that process with an override:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install\setup_gpu.ps1 -DryRun
```

For the env-only loader, use a process-scoped execution policy in the **current**
PowerShell session if required: `Set-ExecutionPolicy -Scope Process Bypass`, then
run `load_env.ps1` and Maven there. Launching the loader in a separate PowerShell
process cannot set environment variables in its parent terminal.

Windows runtime loading registers the selected environment's DLL directories;
ASR uses its PyTorch CUDA/cuDNN DLLs. For pyannote, the worker reads normalized
audio with SoundFile and passes waveform tensors directly. It does not require
TorchCodec's file decoder for that route. TorchCodec may warn about unavailable
file decoding on Windows; the installer checks the waveform route actually used
by this application. Standalone scripts that use TorchCodec to decode files
would need a separately working decoder installation. See the
[TorchCodec 0.7 release notes](https://github.com/meta-pytorch/torchcodec/releases/tag/v0.7.0)
for Windows packaging distinctions.

Temporal, MinIO and Ollama still need to be reachable, but the worker itself
does not require Docker or WSL. Native Windows installation/execution has not
been run on a Windows host in this workspace; the platform branches, PowerShell
scripts and waveform input path have targeted checks. Run a short recording on
the Windows destination before submitting a long meeting.

### Windows: optional Ubuntu under WSL2

Open PowerShell **as Administrator** and install Ubuntu:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart Windows if requested, open Ubuntu and create its Linux user account.
Check the distribution from PowerShell:

```powershell
wsl --list --verbose
```

It should show version `2`. If needed, convert the installed distribution using
its exact listed name:

```powershell
wsl --set-version Ubuntu-24.04 2
```

Follow Microsoft's [WSL installation guide](https://learn.microsoft.com/windows/wsl/install)
if the command is unavailable or the host needs additional Windows features.
Inside the **Ubuntu terminal**, run the Linux tutorial above, then the shared
setup. Store the checkout and work directory inside the Linux filesystem, such
as `~/Projects/temporal-worker` and `/tmp/temporal-asr-poc`.

Use Linux paths in `.env`, including `python/venv/bin/python`; do not use
`C:\\...`, Windows `Scripts/python.exe`, or Windows FFmpeg executable paths.
All `source .env`, pip, setup-script and Maven commands below run in Ubuntu,
not PowerShell or Command Prompt. Git Bash alone does not provide this runtime.

Prefer running the dependent services in the same WSL environment or on reachable
network hosts. When a service runs on Windows, verify its host address and port
from WSL; do not assume WSL's `127.0.0.1` reaches it in every network mode. For
NVIDIA support, follow [NVIDIA's CUDA on WSL guide](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)
and then verify CUDA from the selected Python environments. CPU setup works
without configuring CUDA.

### macOS: Apple Silicon native CPU setup

Install [Homebrew](https://brew.sh/) first using its official instructions, then
run in Terminal:

```bash
brew install git openjdk@21 maven ffmpeg python@3.12 libsndfile
export JAVA_HOME="$(brew --prefix openjdk@21)/libexec/openjdk.jdk/Contents/Home"
export PATH="$JAVA_HOME/bin:$(brew --prefix python@3.12)/bin:$PATH"
git clone https://github.com/lqmnwido/asr-tools.git
cd asr-tools
mvn -version
python3.12 --version
```

`mvn -version` should report Java 21. Set those exports in each new terminal or
your shell startup file. The Homebrew prefix varies by architecture, so use
`brew --prefix` and `command -v` rather than copying `/opt/homebrew` blindly.

Install both native CPU environments using the installer in `install/`:

```bash
bash install/setup_cpu.sh --dry-run
bash install/setup_cpu.sh
```

The installer selects native macOS wheels and checks both environments. Before
starting the worker, edit these values in `.env`:

```dotenv
PYTHON_EXECUTABLE=python/venv/bin/python
DIARIZATION_PYTHON=python/diarization-venv/bin/python
DIARIZATION_DEVICE=cpu
ASR_WORK_DIR=/tmp/temporal-asr-poc
```

Set `FFMPEG_PATH` and `FFPROBE_PATH` to the actual paths printed by:

```bash
command -v ffmpeg
command -v ffprobe
```

The Linux defaults `/usr/bin/ffmpeg` and `/usr/bin/ffprobe` generally do not match
Homebrew's installation. Do not set `DIARIZATION_DEVICE=cuda` or `mps` expecting
this tutorial to enable Apple GPU acceleration. Native Mac execution and its
package combination have not been tested in this workspace; run the import and
short-recording checks before a long job. Use the container route if native
dependency installation fails. On Intel Macs, use a Linux container or Ubuntu
VM rather than weakening the pinned speaker dependencies.

### Linux container: Windows, macOS or Linux hosts

Install [Docker Desktop](https://docs.docker.com/desktop/) on Windows/macOS, or
[Docker Engine](https://docs.docker.com/engine/install/) on Linux. Windows must
use Linux containers. The commands below run in Bash/WSL or macOS Terminal.

From the repository root:

```bash
docker build --platform linux/amd64 -f dockerfile -t temporal-worker:local .
cp -n .env.example .env.container
```

Edit `.env.container` separately from a host/native `.env`. Docker's env-file
syntax is plain `KEY=value`: do not add `export` or wrap values in shell quotes,
which Docker would retain as literal characters. Set real credentials and token,
then use these **container** paths and reachable service addresses:

```dotenv
SERVER_PORT=8082
PYTHON_EXECUTABLE=/app/python/venv/bin/python
DIARIZATION_PYTHON=/app/python/diarization-venv/bin/python
FFMPEG_PATH=/usr/bin/ffmpeg
FFPROBE_PATH=/usr/bin/ffprobe
ASR_WORK_DIR=/tmp/temporal-asr-poc
DIARIZATION_DEVICE=cpu
TEMPORAL_TARGET=host.docker.internal:7233
MINIO_URL=http://host.docker.internal:9000
OLLAMA_HOST=http://host.docker.internal:11434
```

Those addresses assume services are on the Docker host and exposed to the
container. For services in another container, use their Docker network DNS names
on a shared network; for remote services, use their actual network addresses.
Services bound only to host loopback may need network configuration before a
container can connect. `127.0.0.1` inside this worker container is the worker
container itself.

```bash
docker run --name temporal-worker --platform linux/amd64 \
  --add-host=host.docker.internal:host-gateway \
  --env-file .env.container \
  -p 8082:8082 \
  -v temporal-worker-audio:/tmp/temporal-asr-poc \
  -v temporal-worker-models:/root/.cache \
  temporal-worker:local
```

The Dockerfile installs its own Java/Python/FFmpeg dependencies and both Python
environments. Named volumes keep audio work files and cached model downloads
between container replacements. The `linux/amd64` target is a common Linux
runtime for this tutorial; on Apple Silicon it uses emulation and can be slow.
This CPU tutorial does not configure NVIDIA container access. It does not start
Temporal, MinIO or Ollama, and the image build/container startup has not been
verified on every host OS.

To inspect or stop that container from another terminal:

```bash
docker logs -f temporal-worker
docker stop temporal-worker
```

For a Windows PowerShell host terminal, use the same Docker arguments on a
**single line**; Bash backslash line continuations do not work in PowerShell.
See [Docker's container run documentation](https://docs.docker.com/engine/containers/run/)
for platform-specific invocation details. `.env.container` is ignored by Git.

### What has been verified

Source checks, Python tests, Java tests and short audio processing were run in the
current Linux workspace. Native Windows paths, DLL registration, installer
selection and PowerShell syntax/routing have targeted checks from Linux.
Windows/WSL, macOS and Docker sections are installation tutorials based on the
code and upstream documentation, not completed runtime
certifications. Validate imports, tool paths, service connections and a short
recording on the destination host before relying on it for long meetings.

## Setup and loading `.env`

Run from the repository root. Use Java 21, Maven, FFmpeg/FFprobe and Python 3.12
for the current local runtime. Temporal, MinIO and Ollama must be reachable from
the worker host. Their servers are separate from this application.

For a new checkout, create `.env` once:

```bash
cp -n .env.example .env
```

For an existing installation, merge the settings below into your existing
`.env`; do not replace working service addresses or credentials with example
values. `.env` is ignored by Git. `.env.example` contains placeholders only.

The [install guide](install/README.md) covers CPU/GPU selection, PowerShell/WSL
wrappers and requirements-only installation. If setup is needed, choose one
installation mode for both environments. Use the Bash commands below on
Linux, macOS, or WSL, or follow the PowerShell tutorial for native Windows.

```bash
bash install/setup_cpu.sh --dry-run
bash install/setup_cpu.sh
```

For NVIDIA Linux/WSL2 GPU setup instead:

```bash
bash install/setup_gpu.sh --dry-run
bash install/setup_gpu.sh
```

The GPU installer checks the driver and GPU before selecting CUDA 12.6, 12.8 or
12.9 requirements. It does not install the system driver or edit `.env`. It
prints the required runtime paths and device value after setup. On macOS use
the CPU installer. For the container route, the Dockerfile runs CPU setup;
skip host environment creation.

The speaker installer defaults to CPU PyTorch. Keep these two environments
separate: the speaker runtime uses NumPy 2 and newer PyTorch, while the main ASR
requirements use NumPy below 2. First use may download model weights.

Installer entry points and dependency lists are grouped under `install/`:

| File | Use |
| --- | --- |
| `install/setup_cpu.sh` | Linux/WSL2 and Apple Silicon macOS CPU setup. |
| `install/setup_gpu.sh` | NVIDIA Linux/WSL2 setup with driver/GPU detection and compatible CUDA selection. |
| `install/setup_cpu.ps1`, `install/setup_gpu.ps1` | Native Windows installers; add `-UseWSL` for the optional WSL route. |
| `install/start_worker.ps1` | Native Windows startup with dotenv loading and tool-path resolution. |
| `install/load_env.sh`, `install/load_env.ps1` | Load `.env` only into the current terminal; run Maven yourself afterward. |
| `install/env.windows.example` | Native Windows configuration template. |
| `install/requirements.txt` | Shared ASR application dependencies. |
| `install/requirements-diarization.txt` | Shared speaker application dependencies. |
| `install/requirements-pytorch-*` | Explicit CPU or CUDA PyTorch/TorchAudio/TorchVision profiles. |
| `install/requirements-diarization-pytorch-*` | Corresponding speaker profiles, including TorchCodec. |

Use `--component asr` or `--component diarization` to update just one environment.
The GPU dry-run reports the detected hardware and requirements before installing.
It selects among CUDA 12.6, 12.8 and 12.9, not the newest arbitrary CUDA release.
If hardware or driver checks fail, it stops rather than silently installing CPU
packages. Existing NVIDIA drivers are never changed. See the
[install guide](install/README.md) for selection limits and manual pip commands.

Run `mvn spring-boot:run` from the repository root. Spring Boot reads the
repository's `.env` as a properties file. Use plain `KEY=value` lines without
shell quotes or `export`. Restart Maven after editing the file. Exported
environment variables take precedence.

For a custom file or main Python environment:

```bash
source install/load_env.sh --env-file /path/to/worker.env --python-executable /path/to/venv/bin/python
```

The loaders do not change the saved `.env` file.

Alternatively, export a shell-compatible `.env` before starting Maven manually:

```bash
set -a
source .env
set +a
mvn spring-boot:run
```

Keep `.env` shell-compatible for this command: quote values containing spaces
or shell characters using single quotes. An exported
environment variable takes precedence over Python's `.env` loading. Stop the
worker, reload the edited file and restart it after changing settings. For
containers or service managers, pass these variables through their environment
configuration instead; `127.0.0.1` then refers to that container or host.

## Model downloads, cache and Hugging Face token

The setup installers download **Python packages**, not pyannote or Whisper model
weights. Their import checks do not call the model-loading pipeline. The main
Whisper model loads when the worker starts; Community-1 loads when speaker
preparation runs. Missing model files are downloaded at those stages.

Before the first Community-1 download, accept its access conditions and set your
own `HF_TOKEN` in `.env`. Package installation does not require that token.

Hugging Face normally stores downloaded files under the runtime user's home:

```text
~/.cache/huggingface/hub/
  models--pyannote--speaker-diarization-community-1/
  ...other model repositories and dependencies...
```

This is persistent storage on disk, not temporary RAM. Reusing the downloaded
weights avoids downloading them on every job. Multiple Python environments
running as the same user can share it. Removing a virtual environment normally
does not remove this cache. `HF_HOME` or `HF_HUB_CACHE` can override its location;
changing the path does not move existing downloads automatically.

The token is for authenticated downloads, not the actual inference computation.
Remove it only after confirming all required models load and a short job works
offline in the same runtime. A model may reference other cached files, and an
online model-ID lookup can still contact the Hub. The
[offline check in the install guide](install/README.md#hugging-face-token-and-model-downloads)
disables Hub downloads and implicit saved tokens while checking local loading.
Use `HF_HUB_OFFLINE=1` for a tested cache-only deployment, or configure a complete
tested local directory with `DIARIZATION_MODEL`. Clear any already-exported
token and restart the worker after removing it from `.env`.

A new user/container, another machine, a changed model or a cleared cache may
need authenticated downloads again. The Docker tutorial mounts
`temporal-worker-models` at `/root/.cache` to preserve its model cache. Audio
working files under `ASR_WORK_DIR` and installed packages in the two virtual
environments are separate disk usage from the model cache.

## Required `.env` edits

These are the settings to check before running a job:

| Setting | Example/default | What to edit |
| --- | --- | --- |
| `SERVER_PORT` | `8082` | Change if the worker HTTP port is already occupied. |
| `TEMPORAL_TARGET` | `127.0.0.1:7233` | Temporal server gRPC address, without `http://`. Use the deployment's reachable hostname and port. |
| `TEMPORAL_NAMESPACE` | `default` | An existing namespace on that Temporal server. |
| `TEMPORAL_WORKFLOW_TASK_QUEUE` | `ASR_WORKFLOW_QUEUE` | Must match the workflow starter's task queue. |
| `TEMPORAL_MEDIA_TASK_QUEUE` | `MEDIA_TASK_QUEUE` | Must match the job's media task queue. |
| `TEMPORAL_ASR_TASK_QUEUE` | `ASR_TASK_QUEUE` | Must match the job's ASR task queue. |
| `MINIO_URL` | `http://127.0.0.1:9000` | MinIO S3 API URL, **not** the browser console URL. |
| `MINIO_ACCESS_KEY` | Required | Set the access key for your MinIO instance. |
| `MINIO_SECRET_KEY` | Required | Set its corresponding secret key. Keep real credentials only in `.env` or your deployment's secret configuration. |
| `MINIO_BUCKET` | `asr-poc` | Bucket used by the application and submitted jobs. The worker also uses the bucket supplied in each job. |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama endpoint reachable from the worker. |
| `OLLAMA_MODEL` | `llama3.2:3b` | A model installed in that Ollama service, used by default for optional proofreading. |
| `HF_TOKEN` | Empty | Your Hugging Face token for downloading gated Community-1 weights, after accepting the model's access conditions. Never put a real token in `.env.example`. |
| `PYTHON_EXECUTABLE` | `python/venv/bin/python` | Main ASR Python executable; relative paths assume startup from the repository root. |
| `DIARIZATION_PYTHON` | `python/diarization-venv/bin/python` | Separate speaker Python executable created by the setup script. |
| `FFMPEG_PATH` | `/usr/bin/ffmpeg` | Actual FFmpeg executable path. |
| `FFPROBE_PATH` | `/usr/bin/ffprobe` | Actual FFprobe executable path. |
| `ASR_WORK_DIR` | `/tmp/temporal-asr-poc` | Writable location with room for downloads, WAVs, chunks and checkpoints. |

Community-1 access conditions are on its
[model page](https://huggingface.co/pyannote/speaker-diarization-community-1).
An existing local model directory can instead be supplied through
`DIARIZATION_MODEL`. To install the example Ollama model locally:

```bash
ollama pull llama3.2:3b
```

Run that command on the Ollama server if it is hosted elsewhere. Merely setting
`OLLAMA_MODEL` does not install the model.

## ASR and audio settings

These defaults can normally stay as shown:

```dotenv
WHISPER_MODEL=large-v3
ASR_LANGUAGE=ms
ASR_CHUNK_SECONDS=30
ASR_AUDIO_FILTER=auto
ASR_HOTWORDS=
ASR_REVIEW_MODEL=
ASR_MAX_REVIEW_SEGMENTS=8
```

| Setting | Effect and when to change it |
| --- | --- |
| `WHISPER_MODEL` | Main Faster-Whisper checkpoint or local model directory. Explicit `large-v3` keeps model selection consistent across CPU/GPU hosts. Without the variable, current code selects `large-v3` on CPU and `turbo` on CUDA. |
| `ASR_LANGUAGE` | `ms` selects Malay-first decoding; `auto` enables language detection. Compare multilingual recordings before changing it. This is a language setting, not a spelling correction list. |
| `ASR_CHUNK_SECONDS` | Audio chunk length. Chunks overlap by two seconds, so the value must exceed two. Keep `30` unless evaluating another chunk size. |
| `ASR_AUDIO_FILTER` | `auto` enables gentle cleanup and measured loudness normalization. The previous default `loudnorm=I=-23:TP=-2:LRA=11` also selects this profile. Other strings replace the ASR filter only; quote a custom chain when sourcing `.env`. |
| `ASR_HOTWORDS` | Optional decoding hints. Leave empty for automatic contextual proofreading; no manual corrections are required here. Hints can bias recognition. |
| `ASR_REVIEW_MODEL` | Empty reuses the main Whisper checkpoint for suspicious segments. A different Whisper checkpoint/path may be supplied, with extra model loading and memory costs. Agreement between Whisper passes does not prove correctness. |
| `ASR_MAX_REVIEW_SEGMENTS` | Maximum review attempts per audio chunk. Raising it increases processing time. Long suspicious segments can remain pending review. |

ASR device selection is automatic: CUDA when available to the main Python
environment, otherwise CPU. Current compute types are `int8_float16` on CUDA and
`int8` on CPU. There is no `ASR_DEVICE` setting in this implementation. Verify
the main PyTorch runtime with:

```bash
python/venv/bin/python -c 'import torch; print("CUDA available:", torch.cuda.is_available())'
```

Do not add CUDA flags to `.env` expecting them to install drivers or CUDA-enabled
packages. The main ASR environment and isolated speaker environment each need
their own compatible runtime.

## Speaker detection settings

Portable CPU configuration:

```dotenv
ASR_DIARIZATION_BACKEND=pyannote
DIARIZATION_PYTHON=python/diarization-venv/bin/python
DIARIZATION_MODEL=pyannote/speaker-diarization-community-1
DIARIZATION_DEVICE=cpu
DIARIZATION_BATCH_SIZE=1
DIARIZATION_TIMEOUT_SECONDS=5400
DIARIZATION_WINDOW_SECONDS=600
DIARIZATION_OVERLAP_SECONDS=15
DIARIZATION_SPEAKER_THRESHOLD=0.70
DIARIZATION_SPEAKER_MARGIN=0.10
PYANNOTE_METRICS_ENABLED=0
NUM_SPEAKERS=
MIN_SPEAKERS=
MAX_SPEAKERS=
```

| Setting | Effect and when to change it |
| --- | --- |
| `ASR_DIARIZATION_BACKEND` | Keep `pyannote` for the recording-wide timeline. `ecapa` explicitly selects the legacy path; failures do not silently switch backends. |
| `DIARIZATION_MODEL` | Community-1 model ID or existing local model directory. |
| `DIARIZATION_DEVICE` | `cpu` is the portable default. Use `cuda` only after verifying CUDA in the isolated speaker environment. |
| `DIARIZATION_BATCH_SIZE` | Segmentation/embedding batch size. Keep `1` for limited GPU memory; increasing it can cause out-of-memory errors. |
| `DIARIZATION_TIMEOUT_SECONDS` | Speaker subprocess timeout **per window**, not the whole recording. Default `5400` is 90 minutes. This differs from Temporal's activity timeout. |
| `DIARIZATION_WINDOW_SECONDS` | Ten-minute core windows by default, checkpointed independently. Smaller windows reduce individual work units but add overhead and speaker matching boundaries. |
| `DIARIZATION_OVERLAP_SECONDS` | Context on each side of each core window, in seconds. Keep `15` initially. |
| `DIARIZATION_SPEAKER_THRESHOLD` | Minimum embedding similarity for matching speakers across windows. Higher values are more conservative and may split one person into several labels. |
| `DIARIZATION_SPEAKER_MARGIN` | Required separation from an alternative speaker match. Higher values leave more uncertain matches unresolved. |
| `PYANNOTE_METRICS_ENABLED` | `0` disables pyannote metrics reporting. |
| `NUM_SPEAKERS` | Leave empty to estimate. In the windowed pipeline it supplies an upper bound per window when `MAX_SPEAKERS` is empty, rather than forcing every window to contain that many people. |
| `MIN_SPEAKERS` | Used only by standalone full-recording diarization; ignored in windowed mode. |
| `MAX_SPEAKERS` | Optional upper bound per window. Leave empty unless the meeting provides a justified bound. |

For a host with a compatible NVIDIA driver, the GPU installer selects a matching
PyTorch profile. To update only the speaker environment:

```bash
bash install/setup_gpu.sh --component diarization
python/diarization-venv/bin/python -c 'import torch; print("CUDA available:", torch.cuda.is_available())'
```

After that check reports `True`, change `.env` to `DIARIZATION_DEVICE=cuda` and
restart the worker. Changing the device alone cannot turn CPU-only PyTorch into
a CUDA build. The new installers accept `SETUP_PYTHON`, `--asr-venv` and
`--diarization-venv` for custom locations; update `.env` with the printed paths.
The old `python/setup_diarization.sh` remains available for legacy deployment
commands but is not required by the new setup.

## Automatic proofreading settings

```dotenv
ASR_PROOFREAD_ENABLED=true
ASR_PROOFREAD_MODEL=
ASR_PROOFREAD_BUDGET_SECONDS=300
```

No `.env` spelling replacement list is needed. Suggestions use transcript
context and repeated vocabulary, followed by a fresh audio check before an edit
is accepted. This is an additional check, not a guarantee of correct words.

| Setting | Effect and when to change it |
| --- | --- |
| `ASR_PROOFREAD_ENABLED` | `true` enables proofreading before publication; `false` disables it. |
| `ASR_PROOFREAD_MODEL` | Empty inherits `OLLAMA_MODEL`. Set only if another installed Ollama model should handle proofreading. |
| `ASR_PROOFREAD_BUDGET_SECONDS` | Overall proposal/audio-check budget, default five minutes. At most 32 proposals are handled per pass. More time may complete more checks but does not guarantee acceptance. |

Unavailable proofreading leaves the recognized text unchanged and records its
status. Proposed changes and original ASR evidence remain in the detailed process
artifact. Optional meeting context can be supplied as UTF-8 text at
`results/<jobId>/meeting-context.txt` in the job bucket, up to 64 KB; this is not a
PDF or DOCX upload interface.

## Optional speaker names

Leave these empty/default unless you have verified voice samples:

```dotenv
SPEAKER_REFERENCES_PATH=
SPEAKER_IDENTITY_THRESHOLD=0.75
SPEAKER_IDENTITY_MARGIN=0.10
```

`SPEAKER_REFERENCES_PATH` points to a local JSON manifest. Each key is a person's
name and each value is a list of clean mono 16 kHz WAV samples, at least three
seconds each. Sample paths are relative to the manifest:

```json
{
  "Speaker name": ["voices/sample-1.wav", "voices/sample-2.wav"]
}
```

The identity threshold is the minimum similarity for a candidate name; the margin
separates that match from alternatives. These are starting settings requiring
evaluation, not verified identities. Speaker detection works without enrollment
and publishes numbered speaker labels. Recognized words alone do not establish
who spoke them.

## Script path settings

Keep these paths unless relocating the scripts. They are relative to the worker's
startup directory, so start it from the repository root or use absolute paths.

| Variable | Default path |
| --- | --- |
| `DIARIZE_SCRIPT_PATH` | `python/diarize_and_match.py` |
| `REFINE_SCRIPT_PATH` | `python/refine_speakers.py` |

Audio enhancement currently invokes `python/enhance_audio.py` directly; there is
no enhancement-script path variable.

## Applying changes and checking setup

1. Update your existing `.env` with the relevant values above.
2. Install missing environments/models on the worker or Ollama host as appropriate.
3. Stop the worker, export `.env` again and restart it from the repository root.
4. Confirm logs show the expected Whisper checkpoint and `Python Worker is READY`.
5. Run a short recording before submitting a long meeting. Check the transcript
   against the audio and confirm the expected output objects exist in MinIO.

Model, language, audio and speaker settings do not rewrite completed jobs.
Audio cleanup requires normalization to run again; ASR settings require new
transcription; proofreading changes require finalization to run again. Speaker
checkpoints include an audio/settings fingerprint, so compatible windows may be
reused. Editing `.env` does not change the Temporal timeout already scheduled in
an existing activity history.

Common setup symptoms:

| Symptom | Check |
| --- | --- |
| Java connects to an unexpected server | Export `.env` before starting Maven; Python loading it does not configure Java. |
| MinIO browser works but object access fails | Use the S3 API endpoint in `MINIO_URL`; check the job bucket and credentials. |
| `NoSuchKey` for a transcript chunk | The requested object is absent in the specified bucket. Check the chunk-producing activity and actual keys; changing the audio filter will not recreate it. |
| Community-1 authorization/download error | Model access conditions, `HF_TOKEN`, connectivity or a valid local model directory. |
| CUDA unavailable or CPU-only runtime error | Check CUDA inside the exact Python environment selected for that component. |
| Long `NORMALIZING` stage | Check speaker-window logs: this label also covers `PrepareRecording`, not just FFmpeg. |
| Missing spoken text | Inspect `transcript.process.json` and `review.json` for withheld segments, then compare the audio. |

## Audio enhancement

Normalization now creates a gently cleaned ASR WAV and a separate listening
copy with speech EQ, compression and measured loudness normalization. The
original upload is preserved, and neither copy removes silence or changes
playback speed.

Each new normalization writes these objects to MinIO:

- `normalized/<jobId>/audio.wav`: mono 16 kHz input for ASR and diarization.
- `normalized/<jobId>/audio-enhanced.m4a`: enhanced listening download.
- `normalized/<jobId>/audio-enhancement.json`: processing measurements and settings.

The default profile works without editing `.env`, downloading models or using
additional GPU memory. Existing recordings need normalization to run again.
Clearer sound does not guarantee more accurate recognition; compare the original
and enhanced recording when reviewing uncertain words.

## Workflow status labels

Both preparation activities use `"currentStep": "NORMALIZING"`:

| Temporal activity | Work performed | Current step |
| --- | --- | --- |
| `ValidateAndNormalize` | Validate input and normalize audio | `NORMALIZING` |
| `PrepareRecording` | Prepare the recording's speaker timeline using pyannote | `NORMALIZING` |

Previously, `PrepareRecording` reported `DETECTING_SPEAKERS`.
`ValidateAndNormalize` already reported `NORMALIZING`. The shared label changes
the reported workflow status; the activities retain their names and behavior.
Temporal's activity history still distinguishes the two activities.

After preparation, the workflow reports `CHUNKING`, then `TRANSCRIBING` and
`DIARIZING` while processing audio chunks.

Restart the worker after source changes to load the updated status labels.
