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
install -m 644 extension/extension.js extension/metadata.json window.py "$extensions/"
# systemd specifiers and double quotes require escaping inside ExecStart.
escaped_root=$(printf '%s' "$root" | sed 's/\\/\\\\/g; s/"/\\"/g; s/%/%%/g')
cat > "$units/probe.service" <<UNIT
[Unit]
Description=Probe memory pressure monitor
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory="$escaped_root"
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
if command -v gnome-extensions >/dev/null; then
    gnome-extensions enable probe@local || printf '%s\n' 'GNOME 세션에 다시 로그인한 뒤 gnome-extensions enable probe@local 을 실행하세요.'
fi
