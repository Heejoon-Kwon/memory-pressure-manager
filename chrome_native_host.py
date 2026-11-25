#!/usr/bin/python3
"""Chrome bridge: tab titles stay in private runtime files; URLs never leave Chrome."""
import fcntl
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(os.environ.get('XDG_RUNTIME_DIR') or os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'probe'
STATE = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'probe'
MAX_SIZE = 1024 * 1024
COOLDOWN = 90


def service_active():
    try:
        return subprocess.run(['/usr/bin/systemctl', '--user', 'is-active', '--quiet', 'probe.service'],
                              timeout=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def status():
    try:
        data = json.loads((BASE / 'status.json').read_text())
        age = time.time() - float(data['timestamp'])
        running = 0 <= age <= 20 and service_active()
        return {'running': running, 'critical': running and data.get('level') == 'critical'}
    except (OSError, ValueError, TypeError, KeyError):
        return {'running': False, 'critical': False}


def event(kind):
    try:
        from history import record
        record(STATE / 'probe.sqlite3', kind, {})
    except Exception:
        pass


def handle(message):
    kind = message.get('type') if isinstance(message, dict) else None
    if kind == 'sync':
        try:
            from chrome_tabs import sync
            return {'type': 'sync', 'commands': sync(BASE, message.get('profile'), message.get('tabs'))}
        except (ValueError, OSError):
            return {'type': 'sync', 'error': 'sync_failed'}
    if kind == 'status':
        return status()
    if kind == 'report':
        result = message.get('result')
        if result in ('discarded', 'failed'):
            event('tab_' + result)
        return {'ok': True}
    # Old extension versions must not bypass AI/verification in the agent.
    return {'granted': False, 'reason': 'agent_controls_tab_actions'}


def main():
    while True:
        header = sys.stdin.buffer.read(4)
        if len(header) != 4:
            return
        size = struct.unpack('=I', header)[0]
        if size > MAX_SIZE:
            return
        try:
            raw = sys.stdin.buffer.read(size)
            if len(raw) != size:
                return
            response = handle(json.loads(raw))
        except (ValueError, UnicodeDecodeError):
            response = {'error': 'invalid_request'}
        output = json.dumps(response, ensure_ascii=False).encode()
        sys.stdout.buffer.write(struct.pack('=I', len(output)) + output)
        sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
