import io
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import chrome_native_host as host
from agents import ResponseAgent, group_apps, MIB
from test_probe import process


class NativeHostTests(unittest.TestCase):
    def test_status_rejects_stale_and_stopped_service(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(host, 'BASE', Path(tmp)):
            (Path(tmp) / 'status.json').write_text(json.dumps({'timestamp': 100, 'level': 'critical'}))
            with patch('chrome_native_host.time.time', return_value=105), patch.object(host, 'service_active', return_value=True):
                self.assertEqual(host.status(), {'running': True, 'critical': True})
            with patch('chrome_native_host.time.time', return_value=125), patch.object(host, 'service_active', return_value=True):
                self.assertFalse(host.status()['critical'])
            with patch('chrome_native_host.time.time', return_value=105), patch.object(host, 'service_active', return_value=False):
                self.assertFalse(host.status()['critical'])

    def test_claim_requires_critical_and_applies_global_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(host, 'BASE', Path(tmp)), \
             patch.object(host, 'status', return_value={'running': True, 'critical': True}), \
             patch('chrome_native_host.time.time', return_value=1000):
            self.assertTrue(host.handle({'type': 'claim'})['granted'])
            self.assertFalse(host.handle({'type': 'claim'})['granted'])
        with tempfile.TemporaryDirectory() as tmp, patch.object(host, 'BASE', Path(tmp)), \
             patch.object(host, 'status', return_value={'running': True, 'critical': False}):
            self.assertFalse(host.handle({'type': 'claim'})['granted'])

    def test_tab_mode_never_closes_whole_chrome(self):
        app = group_apps([process(10, 'chrome')])[0]
        with patch('agents.chrome_tab_management_enabled', return_value=True):
            self.assertEqual(ResponseAgent.candidates([app], {'chrome': 999}), [])
            with self.assertRaises(ValueError):
                ResponseAgent.close(app, {'chrome': 999})
        with patch('agents.chrome_tab_management_enabled', return_value=False):
            self.assertEqual(ResponseAgent.candidates([app], {'chrome': 999})[0].key, 'chrome')

    def test_native_message_framing(self):
        req = json.dumps({'type': 'status'}).encode()
        binary = io.BytesIO(struct.pack('=I', len(req)) + req)
        output = io.BytesIO()
        with patch.object(host.sys, 'stdin', type('Input', (), {'buffer': binary})()), \
             patch.object(host.sys, 'stdout', type('Output', (), {'buffer': output})()), \
             patch.object(host, 'status', return_value={'running': False, 'critical': False}):
            host.main()
        data = output.getvalue()
        self.assertEqual(struct.unpack('=I', data[:4])[0], len(data) - 4)
        self.assertFalse(json.loads(data[4:])['running'])


if __name__ == '__main__':
    unittest.main()
