import json
import os
import tempfile
import unittest
from pathlib import Path
from agents import GIB, Sample
from early_pressure import EarlyPressure
from kernel_guard import change, requests
from unittest.mock import patch


class PreventionTests(unittest.TestCase):
    def test_acts_before_ninety_percent_and_uses_hysteresis(self):
        policy=EarlyPressure({})
        def sample(free): return Sample(16*GIB,int(16*GIB*free/100),0,0,0,0)
        self.assertEqual(policy.classify(sample(24),'normal',0),'warning')
        self.assertEqual(policy.classify(sample(20),'normal',2),'warning')
        self.assertEqual(policy.classify(sample(24),'normal',4),'warning')
        self.assertEqual(policy.classify(sample(28),'normal',6),'normal')

    def test_forecasts_falling_available_memory(self):
        p=EarlyPressure({})
        for t,free in [(0,26),(2,25),(4,24)]:
            level=p.classify(Sample(16*GIB,int(16*GIB*free/100),0,0,0,0),'normal',t)
        self.assertEqual(level,'warning')

    def test_helper_refuses_foreign_uid_and_reused_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); process=root/'100'; process.mkdir()
            (process/'status').write_text('Uid:\t1000\t1000\t1000\t1000\n')
            (process/'stat').write_text('100 (test program) '+ ' '.join(['S']+['0']*18+['1234']))
            (process/'oom_score_adj').write_text('0')
            with self.assertRaises(ValueError): change(100,1001,'1234',-1000,root)
            with self.assertRaises(ValueError): change(100,1000,'9999',-1000,root)
            self.assertEqual((process/'oom_score_adj').read_text(),'0')
            self.assertEqual(change(100,1000,'1234',-1000,root),0)
            self.assertEqual(int((process/'oom_score_adj').read_text()),-1000)

    def test_stale_or_symlink_requests_never_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'request'; file.write_text(json.dumps({'timestamp':0,'processes':[{'pid':100,'start':'1234'}]}))
            with self.assertRaises(TimeoutError): requests(file,os.getuid())
            link=Path(tmp)/'link'; link.symlink_to(file)
            with self.assertRaises(OSError): requests(link,os.getuid())

    def test_pressure_loop_never_waits_on_openai(self):
        from probe import Probe
        from test_probe import process
        from agents import group_apps
        from unittest.mock import Mock
        class Stop:
            n=0
            def is_set(self): return self.n>=4
            def wait(self,_): self.n+=1
        with tempfile.TemporaryDirectory() as tmp, patch('probe.RUNTIME',Path(tmp)), patch('probe.overrides',return_value={'slack':500}):
            owner=Probe({'watch':{'systemd_oom_preference':False},'openai':{'api_key':'test','monthly_budget_usd':1}},Path(tmp)/'db')
            owner.stop=Stop()
            owner.watch.sample=Mock(return_value=Sample(16*GIB,GIB,0,0,0,0))
            owner.analysis.applications=Mock(return_value=group_apps([process(999999,'slack',rss=400)]))
            owner.advisor.advise=Mock(side_effect=AssertionError('API used on hot path'))
            owner.tab_response.tick=Mock(return_value=False)
            owner.reclaim_response.tick=Mock(return_value=False)
            try:
                with patch('probe.ResponseAgent.close') as close, patch('probe.notify'), patch('probe.time.monotonic',side_effect=[0,1,2,3]):
                    owner.run()
                    close.assert_called_once()
                    owner.advisor.advise.assert_not_called()
            finally:
                owner.executor.shutdown()
    def test_eighty_percent_usage_never_terminates_apps(self):
        from probe import Probe
        from test_probe import process
        from agents import group_apps
        from unittest.mock import Mock
        class Stop:
            n=0
            def is_set(self): return self.n>=4
            def wait(self,_): self.n+=1
        with tempfile.TemporaryDirectory() as tmp, patch('probe.RUNTIME',Path(tmp)), patch('probe.overrides',return_value={'slack':500}):
            owner=Probe({'watch':{'systemd_oom_preference':False},'openai':{'api_key':'test','monthly_budget_usd':1}},Path(tmp)/'db')
            owner.stop=Stop()
            owner.watch.sample=Mock(return_value=Sample(16*GIB,3*GIB,0,0,0,0))
            owner.analysis.applications=Mock(return_value=group_apps([process(999999,'slack',rss=400)]))
            owner.advisor.advise=Mock(side_effect=AssertionError('API used on hot path'))
            owner.tab_response.tick=Mock(return_value=False)
            owner.reclaim_response.tick=Mock(return_value=False)
            try:
                with patch('probe.ResponseAgent.close') as close, patch('probe.notify'), patch('probe.time.monotonic',side_effect=[0,1,2,3]):
                    owner.run()
                    close.assert_not_called()
                    owner.advisor.advise.assert_not_called()
            finally:
                owner.executor.shutdown()

    def test_default_auto_analysis_does_not_spend_after_recovery(self):
        from probe import Probe
        from test_probe import process
        from agents import group_apps
        from unittest.mock import Mock
        class Stop:
            n=0
            def is_set(self): return self.n>=4
            def wait(self,_): self.n+=1
        with tempfile.TemporaryDirectory() as tmp, patch('probe.RUNTIME',Path(tmp)), patch('probe.overrides',return_value={'slack':500}):
            owner=Probe({'watch':{'systemd_oom_preference':False},'openai':{'api_key':'test','monthly_budget_usd':1}},Path(tmp)/'db')
            owner.stop=Stop()
            owner.watch.sample=Mock(side_effect=[Sample(16*GIB,GIB,0,0,0,0)]*3+[Sample(16*GIB,8*GIB,0,0,0,0)])
            owner.analysis.applications=Mock(return_value=group_apps([process(999999,'slack',rss=400)]))
            owner.advisor.advise=Mock(side_effect=AssertionError('API used on hot path'))
            owner.tab_response.tick=Mock(return_value=False)
            owner.reclaim_response.tick=Mock(return_value=False)
            try:
                with patch('probe.ResponseAgent.close') as close, patch('probe.notify'), patch('probe.time.monotonic',side_effect=[0,1,2,3]):
                    owner.run()
                    close.assert_called_once()
                    owner.advisor.advise.assert_not_called()
            finally:
                owner.executor.shutdown()