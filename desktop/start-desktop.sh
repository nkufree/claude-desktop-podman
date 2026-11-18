#!/bin/bash
set -euo pipefail
umask 077
export XDG_RUNTIME_DIR=/tmp/runtime-claude
export GTK_IM_MODULE=fcitx QT_IM_MODULE=fcitx XMODIFIERS=@im=fcitx
mkdir -p "$XDG_RUNTIME_DIR" "$HOME/.config/autostart" "$HOME/Desktop"
chmod 700 "$XDG_RUNTIME_DIR"
/usr/local/bin/configure-desktop
rm -f /tmp/.X1-lock /tmp/.X11-unix/X1
# A persistent home keeps login state, browser profiles, keyring, and protocol
# associations together. configure-desktop refreshes the project shortcuts.
cat > "$HOME/.config/autostart/claude.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Claude Desktop
Exec=/usr/local/bin/launch-claude
Terminal=false
EOF
xdg-mime default container-browser.desktop x-scheme-handler/http
xdg-mime default container-browser.desktop x-scheme-handler/https
xdg-mime default claude-container.desktop x-scheme-handler/claude

pids=()
cleanup() { trap - EXIT TERM INT; kill "${pids[@]}" 2>/dev/null || true; wait || true; }
trap cleanup EXIT
trap 'exit 0' TERM INT
# VNC is available only on container loopback; only noVNC is published.
Xtigervnc :1 -geometry "${DESKTOP_RESOLUTION}" -depth 24 -localhost yes \
    -SecurityTypes None -AlwaysShared -AcceptSetDesktopSize=1 &
pids+=("$!")
for attempt in {1..50}; do
    [ -S /tmp/.X11-unix/X1 ] && break
    kill -0 "${pids[0]}" || exit 1
    sleep 0.1
done
dbus-run-session -- bash -c '
    password_file="$HOME/.config/claude-podman/keyring-password"
    if [ -s "$password_file" ]; then
        eval "$(gnome-keyring-daemon --unlock --components=secrets < "$password_file")"
    else
        eval "$(gnome-keyring-daemon --start --components=secrets)"
    fi
    export GNOME_KEYRING_CONTROL
    exec xfce4-session
' &
pids+=("$!")
websockify --web /usr/share/novnc 6080 127.0.0.1:5901 &
pids+=("$!")
wait -n "${pids[@]}"
