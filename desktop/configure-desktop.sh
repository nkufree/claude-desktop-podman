#!/bin/bash
set -euo pipefail
settings="$HOME/.config/claude-podman"
marker="$settings/fluent-windows-style-v1"
[ ! -f "$marker" ] || exit 0
mkdir -p "$settings" "$HOME/.config/xfce4/xfconf/xfce-perchannel-xml" "$HOME/.config/xfce4/panel"
for file in /usr/local/share/claude-podman/windows-style/*.xml; do
    destination="$HOME/.config/xfce4/xfconf/xfce-perchannel-xml/$(basename "$file")"
    [ ! -f "$destination" ] || cp -n "$destination" "$destination.before-windows-style"
    cp "$file" "$destination"
done
cp /usr/local/share/claude-podman/windows-style/whiskermenu-1.rc "$HOME/.config/xfce4/panel/whiskermenu-1.rc"
for entry in '3:claude-container.desktop' '4:container-browser.desktop' '5:thunar.desktop'; do
    plugin=${entry%%:*}
    desktop_file=${entry#*:}
    mkdir -p "$HOME/.config/xfce4/panel/launcher-$plugin"
    cp "/usr/share/applications/$desktop_file" "$HOME/.config/xfce4/panel/launcher-$plugin/$desktop_file"
done
touch "$marker"
