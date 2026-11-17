#!/bin/bash
set -euo pipefail
binary=$(command -v claude-desktop || command -v claude-desktop-unofficial || true)
if [ -z "$binary" ]; then
    echo 'Claude Desktop executable not found' >&2
    exit 1
fi
# Wait for the user to create/unlock the container keyring before Electron
# chooses its encryption backend. Otherwise its short startup timeout can
# select basic_text while the initial password prompt is still open.
if ! secret-tool lookup application claude-podman purpose keyring-ready >/dev/null; then
    if ! printf 'ready' | secret-tool store --label='Claude container keyring' application claude-podman purpose keyring-ready; then
        echo 'Create or unlock the container keyring, then launch Claude again.' >&2
        exit 1
    fi
fi
args=(--no-sandbox --disable-dev-shm-usage --disable-gpu --lang=en-US --password-store=gnome-libsecret)
[ -z "${DESKTOP_PROXY:-}" ] || args+=("--proxy-server=$DESKTOP_PROXY")
exec "$binary" "${args[@]}" "$@"
