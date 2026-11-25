import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from agents import GIB, Sample
from chrome_tabs import sync
from probe import Skills
from tab_response import RULE, TabResponse

PROFILE = '11111111-1111-4111-8111-111111111111'
TOKEN = '22222222-2222-4222-8222-222222222222'
TAB = {'id': 1, 'token': TOKEN, 'title': 'private title', 'priority': 500, 'supported': True}


class TabResponseTests(unittest.TestCase):
    def test_ai_execute_verify_learn_reuse_and_invalidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db = base / 'skills.db'
            future = Future()
            future.set_result(RULE)
            owner = SimpleNamespace(database=db, skills=Skills(db), advisor=SimpleNamespace(enabled=True),
                                    executor=Mock(), ask_advisor=Mock(), event=Mock(), interval=5,
                                    cooldown=90, verify_seconds=25)
            owner.executor.submit.return_value = future
            controller = TabResponse(owner, base)
            low = Sample(16*GIB, GIB, 0, 0, 17, 3)
            recovered = Sample(16*GIB, 2*GIB, 0, 0, 1, 0)
            sync(base, PROFILE, [TAB])
            self.assertTrue(controller.tick(low, 'critical', 0))  # AI requested
            self.assertNotIn('private title', str(owner.executor.submit.call_args))
            self.assertTrue(controller.tick(low, 'critical', 5))  # action queued
            commands = sync(base, PROFILE, [{**TAB, 'discarded': True}])
            self.assertEqual(commands[0]['action'], 'discard')
            self.assertTrue(controller.tick(recovered, 'normal', 31))
            self.assertEqual(owner.skills.count(), 1)
            sync(base, PROFILE, [TAB])
            self.assertTrue(controller.tick(low, 'critical', 100))  # learned rule reused
            self.assertEqual(owner.executor.submit.call_count, 1)
            self.assertEqual(controller.trial['source'], 'skill')
            sync(base, PROFILE, [{**TAB, 'discarded': True}])
            controller.tick(low, 'critical', 126)  # no observed recovery
            self.assertEqual(owner.skills.count(), 0)

    def test_protection_prevents_ai_and_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db = base / 'skills.db'
            owner = SimpleNamespace(database=db, skills=Skills(db), advisor=SimpleNamespace(enabled=True), executor=Mock())
            controller = TabResponse(owner, base)
            sync(base, PROFILE, [{**TAB, 'priority': 0}])
            self.assertFalse(controller.tick(Sample(16*GIB, GIB, 0, 0, 17, 3), 'critical', 0))
            owner.executor.submit.assert_not_called()
