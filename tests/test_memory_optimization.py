import json
import os
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from dataclasses import replace

from agents import GIB, MIB, Sample, ResponseAgent, group_apps
from memory_reclaim import ReclaimResponse, target_for, RULE
from memory_knowledge import retrieve
from probe import Skills, recovery_verified
from test_probe import process


class OptimizationTests(unittest.TestCase):
    def test_jvm_and_session_helpers_are_not_kill_candidates(self):
        apps = group_apps([process(10,'java',rss=1800),process(20,'(sd-pam)'),
                           process(30,'gsd-disk-utility-notify'),process(40,'update-notifier')])
        self.assertEqual(ResponseAgent.candidates(apps,{}), [])

    def test_partial_real_gain_is_learnable_without_global_recovery(self):
        before = Sample(16*GIB,734*MIB,0,0,17,3)
        after = Sample(16*GIB,782*MIB,0,0,17,3)
        self.assertTrue(recovery_verified(before,after,8,True))
        self.assertFalse(recovery_verified(before,after,8,False))

    def test_zram_setup_refuses_existing_compression_without_mutations(self):
        import configure_memory
        import io
        with patch('configure_memory.sys.argv' if hasattr(configure_memory,'sys') else 'sys.argv',
                   ['configure_memory.py','--apply']), \
             patch('configure_memory.plan',return_value={'can_apply':False,'zswap_enabled':True}), \
             patch('configure_memory.os.geteuid',return_value=0), \
             patch('configure_memory.subprocess.run') as run, \
             patch('sys.stdout',new_callable=io.StringIO), patch('sys.stderr',new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                configure_memory.main()
            run.assert_not_called()

    def test_termination_rule_migration_preserves_evidence(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE skills (scenario TEXT PRIMARY KEY, app TEXT NOT NULL)')
                db.execute('INSERT INTO skills VALUES (?,?)', ('old-context','terminal'))
            rules=Skills(path)
            self.assertEqual(rules.count(),0)
            self.assertIsNone(rules.get('old-context',[]))
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT scenario,app FROM termination_records').fetchall(), [('old-context','terminal')])

    def test_retrieval_is_relevant_and_bounded(self):
        docs = retrieve([{'id':RULE}])
        self.assertTrue(any(d['id']=='reclaim' for d in docs))
        self.assertTrue(all(d['source'].startswith('https://') for d in docs))
        self.assertLessEqual(len(docs),4)

    def test_skill_generalizes_only_with_live_eligible_target(self):
        apps=group_apps([process(10,'chrome'),process(20,'slack')])
        with tempfile.TemporaryDirectory() as tmp:
            skills=Skills(Path(tmp)/'db')
            old=json.dumps(['stall',True,['chrome','slack']]); new=json.dumps(['stall',True,['chrome']])
            skills.save(old,'chrome')
            self.assertIsNone(skills.get(new,apps[:1]))
            self.assertIsNone(skills.get(new,[a for a in apps if a.key=='slack']))
            skills.discard(new)
            self.assertEqual(skills.count(),0)

    def test_reclaim_scope_checks_membership_and_protection(self):
        group=f'/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/app.slice/app-test.scope'
        app=group_apps([replace(process(10,'slack'),cgroup=group)])[0]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); folder=root/group.lstrip('/'); folder.mkdir(parents=True)
            (folder/'cgroup.procs').write_text('10')
            (folder/'memory.reclaim').touch()
            self.assertEqual(target_for(app,{'slack':500},root),folder)
            self.assertIsNone(target_for(app,{'slack':0},root))
            (folder/'cgroup.procs').write_text('10 999')
            self.assertIsNone(target_for(app,{'slack':500},root))

    def test_non_terminating_reclaim_learns_after_measured_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'db'; future=Future(); future.set_result(None)
            owner=SimpleNamespace(database=db,skills=Skills(db),advisor=SimpleNamespace(enabled=False),
                                  executor=Mock(),event=Mock())
            owner.executor.submit.return_value=future
            ctl=ReclaimResponse(owner,{})
            app=group_apps([process(10,'slack',rss=200)])[0]
            low=Sample(16*GIB,1500*MIB,0,0,2,0)
            with patch('memory_reclaim.target_for',return_value=Path('/fake')), patch('probe.overrides',return_value={'slack':500}):
                self.assertTrue(ctl.tick(low,'warning',0,[app],{'slack':500}))
                ctl.tick(low,'warning',1,[app],{'slack':500})
                ctl.tick(Sample(16*GIB,1600*MIB,0,0,2,0),'warning',12,[app],{'slack':500})
                self.assertEqual(owner.skills.count(),1)
