#!/usr/bin/python3
"""Register the unpacked extension ID with Chrome's user native-messaging host."""
import json
import os
import re
import sys
from pathlib import Path

if len(sys.argv) != 2 or not re.fullmatch('[a-p]{32}', sys.argv[1]):
    sys.exit('사용법: ./install-chrome-host.py 크롬확장ID(32자)')
root = Path(__file__).resolve().parent
folder = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'google-chrome/NativeMessagingHosts'
folder.mkdir(mode=0o700, parents=True, exist_ok=True)
manifest = {'name': 'local.probe.tabs', 'description': 'Memory Pressure Manager memory status bridge',
            'path': str(root / 'chrome_native_host.py'), 'type': 'stdio',
            'allowed_origins': ['chrome-extension://' + sys.argv[1] + '/']}
path = folder / 'local.probe.tabs.json'
path.write_text(json.dumps(manifest, indent=2) + '\n')
os.chmod(path, 0o600)
# Protect all Chrome processes from Probe's app-level SIGTERM while tab mode is installed.
state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'probe'
state.mkdir(mode=0o700, parents=True, exist_ok=True)
(state / 'chrome-tab-management').touch(mode=0o600, exist_ok=True)
print('Chrome 연결 설정 완료:', path)
