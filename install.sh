#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
root=$PWD

case ${1:---cpu} in
    --cpu) bash transcribe/install/setup_cpu.sh ;;
    --gpu) bash transcribe/install/setup_gpu.sh ;;
    --reuse-runtime)
        original=${2:?Provide the absolute path to the existing transcribe project}
        export PYTHON_EXECUTABLE="$original/python/venv/bin/python"
        export DIARIZATION_PYTHON="$original/python/diarization-venv/bin/python"
        export KPN_PYTHON_EXECUTABLE=${KPN_PYTHON_EXECUTABLE:-python3}
        "$PYTHON_EXECUTABLE" -c 'import faster_whisper'
        "$DIARIZATION_PYTHON" -c 'import pyannote.audio'
        "$KPN_PYTHON_EXECUTABLE" -c 'import docx'
        KPN_PYTHON_EXECUTABLE=$(command -v "$KPN_PYTHON_EXECUTABLE")
        ;;
    *) printf 'Usage: %s [--cpu | --gpu | --reuse-runtime /path/to/transcribe]\n' "$0" >&2; exit 2 ;;
esac

if [[ ${1:-} != --reuse-runtime ]]; then
    python3 -m venv "$root/.runtime/report"
    KPN_PYTHON_EXECUTABLE="$root/.runtime/report/bin/python"
    "$KPN_PYTHON_EXECUTABLE" -m pip install -r kpn-output/requirements.txt
    PYTHON_EXECUTABLE="$root/transcribe/python/venv/bin/python"
    DIARIZATION_PYTHON="$root/transcribe/python/diarization-venv/bin/python"
fi

for component in asr-api transcribe kpn-output; do
    mvn -B -f "$component/pom.xml" -DskipTests package
done
(cd transcribe-ui && npm ci --prefer-offline --no-audit --no-fund && npm run build)

mkdir -p .runtime
printf '%s\n' "$PYTHON_EXECUTABLE" "$DIARIZATION_PYTHON" "$KPN_PYTHON_EXECUTABLE" > .runtime/python-paths
printf '\nInstallation complete. Start with ./serve.sh\n'
