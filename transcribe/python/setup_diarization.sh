#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
environment_dir="${DIARIZATION_VENV_DIR:-$script_dir/diarization-venv}"
python_binary="${DIARIZATION_SETUP_PYTHON:-$script_dir/venv/bin/python}"
if [[ ! -x "$python_binary" ]]; then
    python_binary="${DIARIZATION_SETUP_PYTHON:-python3}"
fi
"$python_binary" -c 'import sys; assert (3, 10) <= sys.version_info[:2] <= (3, 13), "Use Python 3.10-3.13 for the pinned PyTorch 2.8 runtime"'

"$python_binary" -m venv "$environment_dir"
"$environment_dir/bin/python" -m pip install --no-cache-dir --upgrade pip
# CPU is the default speaker device; do not download CUDA libraries unnecessarily.
# Override for a deployment with a compatible NVIDIA driver.
"$environment_dir/bin/python" -m pip install --no-cache-dir --upgrade 'torch>=2.8,<2.9' 'torchaudio>=2.8,<2.9' 'torchcodec>=0.7,<0.8' \
    --index-url "${DIARIZATION_TORCH_INDEX:-https://download.pytorch.org/whl/cpu}"
"$environment_dir/bin/python" -m pip install --no-cache-dir -r "$script_dir/requirements-diarization.txt"
"$environment_dir/bin/python" -m pip check
"$environment_dir/bin/python" -c 'from pyannote.audio import Pipeline; print("Community-1 runtime imports successfully")'
printf 'Set DIARIZATION_PYTHON=%s/bin/python\n' "$environment_dir"
