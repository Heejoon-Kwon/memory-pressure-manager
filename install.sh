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
mkdir -p "$extensions" "$units"
install -m 644 extension/extension.js extension/metadata.json window.py history.py priorities.py "$extensions/"
# systemd specifiers and double quotes require escaping inside ExecStart.
escaped_root=$(printf '%s' "$root" | sed 's/\\/\\\\/g; s/"/\\"/g; s/%/%%/g')
cat > "$units/probe.service" <<UNIT
[Unit]
Description=Probe memory pressure monitor
After=graphical-session.target

[Service]
Type=simple
ExecStart=:"$escaped_root/.venv/bin/python" "$escaped_root/probe.py" --config "$escaped_root/config.yml"
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable probe.service
systemctl --user restart probe.service
"$root/panel.sh"
