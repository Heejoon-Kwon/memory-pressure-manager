import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import repair_native_host
import stop_old_windows


class DeploymentTests(unittest.TestCase):
    def test_host_repair_preserves_registered_extension_origins(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'XDG_CONFIG_HOME': tmp}):
            path = Path(tmp) / 'google-chrome/NativeMessagingHosts/local.probe.tabs.json'
            path.parent.mkdir(parents=True)
            data = {'name':'local.probe.tabs', 'type':'stdio', 'path':'/old/checkout/host.py',
                    'allowed_origins':['chrome-extension://abcdefghijklmnopabcdefghijklmnop/']}
            path.write_text(json.dumps(data))
            repair_native_host.main()
            updated = json.loads(path.read_text())
            self.assertEqual(updated['allowed_origins'], data['allowed_origins'])
            self.assertTrue(updated['path'].endswith('/chrome_native_host.py'))
            self.assertNotEqual(updated['path'], data['path'])

    def test_retires_only_owned_management_window(self):
        own = os.getuid()
        target = Path(stop_old_windows.__file__).resolve().with_name('window.py')
        def proc(uid, args):
            return SimpleNamespace(info={'uids':SimpleNamespace(real=uid), 'cmdline':args}, terminate=Mock())
        old = proc(own, ['/usr/bin/python3', str(target)])
        unrelated = proc(own, ['/usr/bin/python3', '/other/project/window.py'])
        foreign = proc(own+1, ['/usr/bin/python3', str(target)])
        with patch('stop_old_windows.psutil.process_iter', return_value=[old, unrelated, foreign]):
            stop_old_windows.main()
        old.terminate.assert_called_once()
        unrelated.terminate.assert_not_called()
        foreign.terminate.assert_not_called()
