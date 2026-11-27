import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agents import GIB, MIB, AnalysisAgent, Process, ResponseAgent, Sample, group_apps
from budget import Budget, BudgetExceeded
from history import initialize, record, read_history
from probe import Advisor, Skills, recovery_verified, scenario_for, same_candidates


def process(pid, executable, rss=200, *, parent=1, args=()):
    return Process(pid, parent, float(pid), '/tests/' + executable, (executable, *args),
                   rss * MIB, None)


class ProbeTests(unittest.TestCase):
    def test_terminal_files_and_other_processes_are_tracked_without_double_counting(self):
        from dataclasses import replace
        import os
        terminal = process(100, 'gnome-terminal-server', rss=80)
        shell = process(101, 'bash', rss=20, parent=100)
        files = process(200, 'nautilus', rss=180)
        unknown = process(300, 'my-worker', rss=300)
        system = replace(process(400, 'gnome-shell', rss=500), uid=os.getuid())
        foreign = replace(process(500, 'database-server', rss=700), uid=os.getuid()+1)
        apps = group_apps([terminal, shell, files, unknown, system, foreign])
        self.assertEqual(sum(a.rss for a in apps), 1780*MIB)
        self.assertEqual(next(a for a in apps if a.key == 'terminal').rss, 100*MIB)
        self.assertEqual(next(a for a in apps if a.key == 'files').name, '파일 탐색기')
        for app in apps:
            if app.name in ('gnome-shell', 'database-server'):
                self.assertEqual(ResponseAgent.blocked_reason(app, {app.key:999}), 'system_process')
                with self.assertRaises(ValueError):
                    ResponseAgent.close(app, {app.key:999})
        self.assertIn('files', [a.key for a in ResponseAgent.candidates(apps, {})])

    def test_terminal_with_protected_ide_is_not_closed_indirectly(self):
        terminal = process(10, 'gnome-terminal-server', rss=80)
        shell = process(11, 'bash', rss=20, parent=10)
        idea = process(12, 'java', rss=2000, parent=11, args=('-Didea.platform.prefix=Idea',))
        apps = group_apps([terminal, shell, idea])
        self.assertEqual(ResponseAgent.candidates(apps, {'terminal':999,'idea':0}), [])
        app = next(a for a in apps if a.key == 'terminal')
        self.assertEqual(ResponseAgent.blocked_reason(app, {'terminal':999}), 'protected_descendant')
        with self.assertRaises(ValueError):
            ResponseAgent.close(app, {'terminal':999})

    def test_force_kill_still_obeys_protection_and_pid_identity(self):
        import signal
        app = group_apps([process(10, 'nautilus')])[0]
        with patch('agents.os.pidfd_open', return_value=99), patch('agents.os.close'), \
             patch('agents.signal.pidfd_send_signal') as sent, \
             patch('agents.get_process', return_value=app.leaders[0]), \
             patch('agents.has_protected_descendants', return_value=False):
            with self.assertRaises(ValueError):
                ResponseAgent.close(app, {'files':0}, force=True)
            sent.assert_not_called()
            ResponseAgent.close(app, {'files':500}, force=True)
            sent.assert_called_once_with(99, signal.SIGKILL)

    def test_priority_migration_and_new_order(self):
        import json
        from priorities import read_priorities
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'priorities.json'
            path.write_text(json.dumps({'chrome': 1, 'slack': 2, 'firefox': 3, 'code': 4, 'idea': 0}))
            old = read_priorities(path)
            self.assertEqual(old, {'chrome': 999, 'slack': 500, 'firefox': 1, 'code': 0, 'idea': 0})
            path.write_text(json.dumps({'version': 2, 'apps': {'chrome': 123, 'slack': 123, 'code': 0, 'idea': 1000}}))
            self.assertEqual(read_priorities(path), {'chrome': 123, 'slack': 123, 'code': 0})
            apps = group_apps([process(10, 'chrome'), process(20, 'firefox'), process(30, 'slack')])
            selected = ResponseAgent.candidates(apps, {'chrome': 1, 'firefox': 999, 'slack': 999})
            self.assertEqual([a.key for a in selected], ['firefox', 'slack', 'chrome'])

    def test_ai_result_survives_pressure_change_but_not_candidate_change(self):
        self.assertTrue(same_candidates('["stall", true, ["chrome"]]',
                                        '["low-available", false, ["chrome"]]'))
        self.assertFalse(same_candidates('["stall", true, ["chrome"]]',
                                         '["stall", true, ["slack"]]'))

    def test_diagnostics_redacts_key_and_reports_eligible_apps(self):
        from diagnostics import inspect
        apps = group_apps([process(10, 'chrome'), process(20, 'code')])
        with tempfile.TemporaryDirectory() as tmp, \
                patch('diagnostics.AnalysisAgent.applications', return_value=apps), \
                patch('diagnostics.rates_for', return_value=(0.1, 0.5)):
            report = inspect({'openai': {'api_key': 'super-secret', 'monthly_budget_usd': 1}},
                             {'chrome': 1}, Path(tmp) / 'absent.db')
            self.assertTrue(report['api_key_configured'])
            self.assertNotIn('super-secret', str(report))
            chrome = next(app for app in report['detected_apps'] if app['name'] == 'Google Chrome')
            self.assertTrue(chrome['eligible'])
            self.assertFalse((Path(tmp) / 'absent.db').exists())

    def test_history_preserves_existing_skills_and_records_advice(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'probe.db'
            skills = Skills(db)
            skills.save('["low-available", false, ["chrome"]]', 'chrome')
            self.assertEqual(len(read_history(db)[0]), 1)
            self.assertEqual(read_history(db)[1], [])
            initialize(db)
            record(db, 'ai_proposal', {'app': 'chrome'})
            record(db, 'action_requested', {'app': 'chrome', 'source': 'skill'})
            current, events = read_history(db)
            self.assertEqual(current[0][1], 'chrome')
            self.assertEqual([event[1] for event in events], ['action_requested', 'ai_proposal'])
            self.assertNotIn('api_key', str(events))

    def test_advice_records_success_and_error_without_credentials(self):
        from probe import Probe
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'probe.db'
            guard = Probe({'openai': {'api_key': 'private-key'}}, db)
            try:
                with patch.object(guard.advisor, 'advise', return_value='chrome'):
                    self.assertEqual(guard.ask_advisor('scenario', {}, [{'id': 'chrome'}]), 'chrome')
                with patch.object(guard.advisor, 'advise', side_effect=RuntimeError('private-key')):
                    with self.assertRaises(RuntimeError):
                        guard.ask_advisor('scenario', {}, [{'id': 'chrome'}])
                events = read_history(db)[1]
                self.assertEqual([e[1] for e in events], ['ai_error', 'ai_request', 'ai_proposal', 'ai_request'])
                self.assertNotIn('private-key', str(events))
            finally:
                guard.executor.shutdown()

    def test_advisor_reserves_budget_and_records_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            budget = Budget(Path(tmp) / 'probe.db', 1000000)
            advisor = Advisor({'api_key': 'test', 'model': 'test'}, budget)
            client = MagicMock()
            with patch('probe.rates_for', return_value=(1, 2)), \
                    patch.dict(sys.modules, {'openai': SimpleNamespace(OpenAI=client)}):
                api = client.return_value.__enter__.return_value.responses.create
                api.return_value = SimpleNamespace(output_text='{"app":"chrome"}',
                    usage=SimpleNamespace(input_tokens=10, output_tokens=5))
                self.assertEqual(advisor.advise({'available_mib': 800}, [{'id': 'chrome'}]), 'chrome')
                self.assertEqual(budget.monthly()['estimated_microusd'], 20)
                self.assertNotIn('test', api.call_args.kwargs['input'])
                api.reset_mock()
                self.assertEqual(advisor.advise({}, []), None)
                api.assert_not_called()

    def test_app_names_groups_helpers_and_excludes_session(self):
        chrome = process(10, 'chrome')
        helper = process(11, 'chrome', 400, parent=10, args=('--type=renderer',))
        idea = process(20, 'java', args=('-Didea.platform.prefix=Idea',))
        code = process(30, 'code')
        services = [process(40, 'gnome-shell'), process(41, 'systemd'), process(42, 'java')]
        apps = group_apps([chrome, helper, idea, code, *services])
        self.assertEqual([a.name for a in apps if not a.key.startswith('process:')], ['Google Chrome', 'IntelliJ IDEA', 'VS Code'])
        self.assertEqual(sum(a.rss for a in apps), sum(p.rss for p in [chrome, helper, idea, code, *services]))
        self.assertEqual(apps[0].rss, 600 * MIB)
        self.assertEqual(apps[0].leaders, [chrome])
        self.assertEqual([a.key for a in ResponseAgent.candidates(apps, {'idea':0,'code':0})], ['chrome'])
        self.assertEqual([a.key for a in ResponseAgent.candidates(apps, {'chrome': 2, 'idea': 1, 'code':0}) if not a.key.startswith('process:')],
                         ['chrome', 'idea'])

    def test_multiple_instances_and_opt_out_are_ineligible(self):
        apps = group_apps([process(10, 'chrome'), process(11, 'chrome')])
        self.assertFalse(ResponseAgent.candidates(apps, {'chrome': 1}))
        with self.assertRaises(ValueError):
            ResponseAgent.close(apps[0], {'chrome': 1})

    def test_pid_reuse_and_new_protection_block_a_signal(self):
        app = group_apps([process(10, 'chrome')])[0]
        with patch('agents.os.pidfd_open', return_value=99) as opened, patch('agents.os.close'), \
                patch('agents.signal.pidfd_send_signal') as sent, \
                patch('agents.get_process', return_value=process(10, 'firefox')):
            with self.assertRaises(ValueError):
                ResponseAgent.close(app, {'chrome': 0})
            opened.assert_not_called()
            with self.assertRaises(ProcessLookupError):
                ResponseAgent.close(app, {'chrome': 1})
            sent.assert_not_called()
        with patch('agents.os.pidfd_open', return_value=99), patch('agents.os.close'), \
                patch('agents.signal.pidfd_send_signal') as sent, \
                patch('agents.get_process', return_value=app.leaders[0]):
            ResponseAgent.close(app, {'chrome': 1})
            sent.assert_called_once()

    def test_persistent_pressure_and_verification(self):
        agent = AnalysisAgent(8, 3)
        low = Sample(16*GIB, GIB, 0, 0, 17, 3)
        ample = Sample(16*GIB, 3*GIB, 0, 0, 1, 0)
        self.assertEqual([agent.pressure(low) for _ in range(3)], ['warning', 'warning', 'critical'])
        self.assertEqual(agent.pressure(ample), 'normal')
        self.assertEqual(agent.pressure(low), 'warning')
        self.assertTrue(recovery_verified(low, ample, 8, True))
        self.assertFalse(recovery_verified(low, ample, 8, False))
        self.assertFalse(recovery_verified(low, low, 8, True))

    def test_skill_survives_restart_and_is_only_used_if_eligible(self):
        low = Sample(16*GIB, GIB, 0, 0, 17, 3)
        apps = group_apps([process(10, 'chrome'), process(11, 'firefox')])
        scenario = scenario_for(low, None, apps)
        self.assertEqual(scenario, scenario_for(low, None, list(reversed(apps))))
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'probe.db'
            skills = Skills(db)
            skills.save(scenario, 'chrome')
            self.assertEqual(Skills(db).get(scenario, apps).key, 'chrome')
            self.assertIsNone(skills.get(scenario, [a for a in apps if a.key != 'chrome']))
            skills.discard(scenario)
            self.assertIsNone(skills.get(scenario, apps))
            budget = Budget(db, 0)
            with self.assertRaises(BudgetExceeded):
                budget.reserve(1)


if __name__ == '__main__':
    unittest.main()
