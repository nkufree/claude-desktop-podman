#!/bin/bash
set -euo pipefail
args=(--no-sandbox --disable-dev-shm-usage --no-first-run --no-default-browser-check --lang=en-US)
[ -z "${DESKTOP_PROXY:-}" ] || args+=("--proxy-server=$DESKTOP_PROXY")
exec chromium "${args[@]}" "$@"
