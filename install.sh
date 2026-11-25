#!/bin/sh
set -eu
cd "$(dirname "$0")"
root="$(pwd -P)"
umask 077
if [ ! -e config.yml ]; then
    (set -C; cat config.example.yml > config.yml)
fi
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
extensions="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/probe@local"
units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$extensions/assets" "$units"
install -m 644 extension/extension.js extension/metadata.json window.py history.py priorities.py chrome_tabs.py response_labels.py "$extensions/"
install -m 644 assets/*.svg "$extensions/assets/"
# systemd specifiers and double quotes require escaping inside ExecStart.
escaped_root=$(printf '%s' "$root" | sed 's/\\/\\\\/g; s/"/\\"/g; s/%/%%/g')
# Preserve the legacy service name as an alias, so two monitors cannot run.
systemctl --user stop probe.service memora.service 2>/dev/null || true
systemctl --user disable probe.service 2>/dev/null || true
if [ -f "$units/probe.service" ] && [ ! -L "$units/probe.service" ]; then
    cp -p "$units/probe.service" "$units/probe.service.before-memora"
fi
cat > "$units/memora.service" <<UNIT
[Unit]
Description=Memora memory manager
After=graphical-session.target

[Service]
Type=simple
ExecStart=:"$escaped_root/.venv/bin/python" "$escaped_root/memora.py" --config "$escaped_root/config.yml"
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes

[Install]
WantedBy=default.target
Alias=probe.service
UNIT
ln -sfn memora.service "$units/probe.service"
systemctl --user daemon-reload
systemctl --user enable memora.service
systemctl --user restart memora.service
"$root/panel.sh"
