#!/usr/bin/env bash
set -euo pipefail
setup_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$setup_root/setup_common.sh"
exec "$setup_python" "$setup_root/setup_runtime.py" --mode cpu "$@"
