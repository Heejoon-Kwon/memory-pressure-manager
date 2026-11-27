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
    def test_tab_snapshot_and_command_are_local_and_guarded_by_session_token(self):
        import chrome_tabs
        profile = '11111111-1111-4111-8111-111111111111'
        token = '22222222-2222-4222-8222-222222222222'
        other = '33333333-3333-4333-8333-333333333333'
        tabs = [{'id': 7, 'token': token, 'title': 'Example', 'priority': 0, 'supported': True}]
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(chrome_tabs.sync(tmp, profile, tabs), [])
            self.assertEqual(chrome_tabs.snapshots(tmp)[0]['tabs'][0]['title'], 'Example')
            with self.assertRaises(ValueError):
                chrome_tabs.enqueue(tmp, profile, 7, other, 999)
            chrome_tabs.enqueue(tmp, profile, 7, token, 999)
            changes = chrome_tabs.sync(tmp, profile, tabs)
            self.assertEqual(changes, [{'id': 7, 'token': token, 'priority': 999}])
            self.assertEqual(chrome_tabs.sync(tmp, profile, tabs), [])
            self.assertNotIn('url', str(chrome_tabs.snapshots(tmp)))

    def test_favicon_metadata_never_accepts_network_urls(self):
        from chrome_tabs import clean_tabs
        item = {'id': 1, 'token': '22222222-2222-4222-8222-222222222222',
                'title': 'Example', 'priority': 500, 'favicon': 'https://example.com/icon.png'}
        self.assertEqual(clean_tabs([item])[0]['favicon'], '')
        item['favicon'] = 'a' * 30000
        self.assertEqual(clean_tabs([item])[0]['favicon'], '')

    def test_status_rejects_stale_and_stopped_service(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(host, 'BASE', Path(tmp)):
            (Path(tmp) / 'status.json').write_text(json.dumps({'timestamp': 100, 'level': 'critical', 'termination_allowed': True, 'discard_allowed': True}))
            with patch('chrome_native_host.time.time', return_value=105), patch.object(host, 'service_active', return_value=True):
                self.assertEqual(host.status(), {'running': True, 'critical': True, 'discard_allowed':True})
            with patch('chrome_native_host.time.time', return_value=125), patch.object(host, 'service_active', return_value=True):
                self.assertFalse(host.status()['critical'])
            with patch('chrome_native_host.time.time', return_value=105), patch.object(host, 'service_active', return_value=False):
                self.assertFalse(host.status()['critical'])

    def test_claim_requires_critical_and_applies_global_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(host, 'BASE', Path(tmp)), \
             patch.object(host, 'status', return_value={'running': True, 'critical': True, 'discard_allowed':True}), \
             patch('chrome_native_host.time.time', return_value=1000):
            self.assertFalse(host.handle({'type': 'claim'})['granted'])
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
