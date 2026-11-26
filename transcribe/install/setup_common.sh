#!/usr/bin/env bash
# Sourced by the root setup entry points. Never install system packages here.
setup_python="${SETUP_PYTHON:-}"
if [[ -z "$setup_python" ]]; then
    for candidate in python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c \
            'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)'; then
            setup_python="$candidate"
            break
        fi
    done
fi
if [[ -z "$setup_python" ]]; then
    echo 'Install Python 3.10-3.12, or set SETUP_PYTHON to its executable.' >&2
    exit 1
fi
