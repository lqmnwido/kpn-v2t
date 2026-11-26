#!/usr/bin/env bash
# Source this file so exports remain in the terminal that will run Maven.
if [[ -z "${BASH_VERSION:-}" ]]; then
    echo 'Open Bash first with: bash. Then run: source install/load_env.sh' >&2
    return 1
fi
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo 'Run: source install/load_env.sh (then mvn spring-boot:run)' >&2
    exit 2
fi

_worker_load_environment() {
    local installer_dir repository_dir worker_python worker_env_file exports
    installer_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || return 1
    repository_dir="$(cd -- "$installer_dir/.." && pwd)" || return 1
    worker_python="$repository_dir/python/venv/bin/python"
    worker_env_file="$repository_dir/.env"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --env-file|--python-executable)
                if [[ $# -lt 2 ]]; then echo "$1 requires a path" >&2; return 1; fi
                if [[ "$1" == "--env-file" ]]; then worker_env_file="$2"; else worker_python="$2"; fi
                shift 2
                ;;
            *) echo 'Usage: source install/load_env.sh [--env-file PATH] [--python-executable PATH]' >&2; return 1 ;;
        esac
    done
    if [[ ! -x "$worker_python" ]]; then
        echo 'Main Python environment missing; run setup first or pass --python-executable.' >&2
        return 1
    fi
    exports="$("$worker_python" "$installer_dir/environment_exports.py" --env-file "$worker_env_file" --format bash)" || return 1
    eval "$exports" || return 1
    echo 'Settings loaded into this terminal. Run: mvn spring-boot:run'
}

if _worker_load_environment "$@"; then
    unset -f _worker_load_environment
else
    unset -f _worker_load_environment
    return 1
fi
