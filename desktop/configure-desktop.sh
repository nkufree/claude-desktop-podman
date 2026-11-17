#!/bin/bash
set -euo pipefail
settings="$HOME/.config/claude-podman"
marker="$settings/fluent-windows-style-v1"
# Refresh project shortcuts even when this persistent home already has a theme.
mkdir -p "$HOME/Desktop"
for entry in '3:claude-container.desktop:Claude.desktop' '4:container-browser.desktop:Chromium.desktop'; do
    plugin=${entry%%:*}
    files=${entry#*:}
    desktop_file=${files%%:*}
    shortcut=${files#*:}
    mkdir -p "$HOME/.config/xfce4/panel/launcher-$plugin"
    cp "/usr/share/applications/$desktop_file" "$HOME/.config/xfce4/panel/launcher-$plugin/$desktop_file"
    cp "/usr/share/applications/$desktop_file" "$HOME/Desktop/$shortcut"
    chmod +x "$HOME/Desktop/$shortcut"
done
[ ! -f "$marker" ] || exit 0
mkdir -p "$settings" "$HOME/.config/xfce4/xfconf/xfce-perchannel-xml" "$HOME/.config/xfce4/panel"
for file in /usr/local/share/claude-podman/windows-style/*.xml; do
    destination="$HOME/.config/xfce4/xfconf/xfce-perchannel-xml/$(basename "$file")"
    [ ! -f "$destination" ] || cp -n "$destination" "$destination.before-windows-style"
    cp "$file" "$destination"
done
cp /usr/local/share/claude-podman/windows-style/whiskermenu-1.rc "$HOME/.config/xfce4/panel/whiskermenu-1.rc"
mkdir -p "$HOME/.config/xfce4/panel/launcher-5"
cp /usr/share/applications/thunar.desktop "$HOME/.config/xfce4/panel/launcher-5/thunar.desktop"
touch "$marker"
