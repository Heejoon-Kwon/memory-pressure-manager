import tempfile
import unittest
from unittest.mock import patch

from agents import MIB, ResponseAgent, group_apps
from response_status import explain
from test_probe import process


class ResponseStatusTests(unittest.TestCase):
    def test_unprotected_does_not_mean_no_permission(self):
        apps = group_apps([process(10, 'chrome'), process(11, 'chrome'), process(12, 'slack', rss=80)])
        with tempfile.TemporaryDirectory() as tmp, patch('agents.chrome_tab_management_enabled', return_value=False):
            detail = explain(apps, {'chrome': 999, 'slack': 200}, set(), tmp)
        self.assertIn('종료 허용 앱은 있으나', detail['summary'])
        self.assertEqual({x['reason'] for x in detail['blocked']}, {'ambiguous_process', 'small_memory'})
        self.assertEqual(ResponseAgent.candidates(apps, {'chrome': 999, 'slack': 200}), [])

    def test_tabs_are_separate_from_app_termination_candidates(self):
        app = group_apps([process(10, 'chrome')])[0]
        tab = {'priority': 500, 'supported': True, 'active': False, 'pinned': False,
               'audible': False, 'discarded': False}
        with patch('agents.chrome_tab_management_enabled', return_value=True), \
             patch('response_status.snapshots', return_value=[{'tabs': [tab]}]):
            detail = explain([app], {'chrome': 500}, set(), '/unused')
            self.assertEqual(detail['tabs']['eligible'], 1)
            self.assertIn('탭 1개 회수 가능', detail['summary'])
            self.assertEqual(detail['blocked'][0]['reason'], 'chrome_tab_mode')
        tab['active'] = True
        with patch('agents.chrome_tab_management_enabled', return_value=True), \
             patch('response_status.snapshots', return_value=[{'tabs': [tab]}]):
            detail = explain([app], {'chrome': 500}, set(), '/unused')
            self.assertEqual(detail['tabs']['eligible'], 0)
            self.assertEqual(detail['tabs']['waiting'], 1)
            self.assertIn('일시 제외', detail['summary'])

    def test_failed_app_explained_without_expanding_candidates(self):
        app = group_apps([process(20, 'slack')])[0]
        with tempfile.TemporaryDirectory() as tmp:
            detail = explain([app], {'slack': 700}, {'slack'}, tmp)
        self.assertEqual(detail['blocked'][0]['reason'], 'previous_attempt_failed')


if __name__ == '__main__':
    unittest.main()
