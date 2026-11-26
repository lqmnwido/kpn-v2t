#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
root=$PWD

if [[ ! -f .runtime/python-paths || ! -f transcribe-ui/dist/index.html ]]; then
    printf 'Run ./install.sh first.\n' >&2
    exit 1
fi
mapfile -t runtime < .runtime/python-paths
export PYTHON_EXECUTABLE=${PYTHON_EXECUTABLE:-${runtime[0]}}
export DIARIZATION_PYTHON=${DIARIZATION_PYTHON:-${runtime[1]}}
export KPN_PYTHON_EXECUTABLE=${KPN_PYTHON_EXECUTABLE:-${runtime[2]}}
java_bin=${JAVA_HOME:+$JAVA_HOME/bin/}java
mkdir -p .logs
pids=()

cleanup() {
    if (("${#pids[@]}")); then
        kill "${pids[@]}" 2>/dev/null || true
        wait "${pids[@]}" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

start() {
    local component=$1
    shift
    (cd "$root/$component" && exec "$@") > "$root/.logs/$component.log" 2>&1 &
    pids+=("$!")
}

export KPN_UI_PORT=${KPN_UI_PORT:-5173}
for component in asr-api transcribe kpn-output; do
    jars=("$component"/target/*.jar)
    if [[ ${#jars[@]} != 1 || ! -f ${jars[0]} ]]; then
        printf 'Expected one packaged JAR for %s. Run ./install.sh.\n' "$component" >&2
        exit 1
    fi
    start "$component" "$java_bin" -jar "$root/${jars[0]}"
done

start transcribe-ui node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port "$KPN_UI_PORT" --strictPort
printf 'Starting KPN V2T at http://localhost:%s. Logs are in %s/.logs.\n' "$KPN_UI_PORT" "$root"
status=0
wait -n -p finished "${pids[@]}" || status=$?
printf 'A KPN V2T service exited (PID %s, status %s). See .logs for details.\n' "${finished:-unknown}" "$status" >&2
exit "$((status == 0 ? 1 : status))"
