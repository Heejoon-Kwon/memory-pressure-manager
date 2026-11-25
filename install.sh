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
install -m 644 extension/extension.js extension/metadata.json window.py history.py priorities.py chrome_tabs.py response_labels.py build_info.py "$extensions/"
install -m 644 assets/*.svg "$extensions/assets/"
# systemd specifiers and double quotes require escaping inside ExecStart.
escaped_root=$(printf '%s' "$root" | sed 's/\\/\\\\/g; s/"/\\"/g; s/%/%%/g')
# Preserve previous service names as aliases; only one monitor is started.
systemctl --user stop probe.service memora.service memory-pressure-manager.service 2>/dev/null || true
systemctl --user disable probe.service memora.service 2>/dev/null || true
for legacy in probe memora; do
    if [ -f "$units/$legacy.service" ] && [ ! -L "$units/$legacy.service" ]; then
        cp -p "$units/$legacy.service" "$units/$legacy.service.before-memory-pressure-manager"
    fi
done
cat > "$units/memory-pressure-manager.service" <<UNIT
[Unit]
Description=Memory pressure manager
After=graphical-session.target

[Service]
Type=simple
ExecStart=:"$escaped_root/.venv/bin/python" "$escaped_root/memory_pressure_manager.py" --config "$escaped_root/config.yml"
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes

[Install]
WantedBy=default.target
Alias=probe.service memora.service
UNIT
ln -sfn memory-pressure-manager.service "$units/probe.service"
ln -sfn memory-pressure-manager.service "$units/memora.service"
# Close only this project's management windows, so old imported UI code is not left running.
if [ -x "$root/.venv/bin/python" ]; then
    "$root/.venv/bin/python" "$root/stop_old_windows.py"
fi
# Repoint an already-registered native host to this checkout without changing extension IDs.
if [ -x "$root/.venv/bin/python" ]; then
    "$root/.venv/bin/python" "$root/repair_native_host.py"
fi
systemctl --user daemon-reload
systemctl --user enable memory-pressure-manager.service
systemctl --user restart memory-pressure-manager.service
"$root/panel.sh"
