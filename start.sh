#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export HOST="${HOST:-127.0.0.1}"
export PYTHONDONTWRITEBYTECODE=1
exec python3 -m punchscope
