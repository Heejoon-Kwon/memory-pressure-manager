"""Run Probe's watch, analysis and response loop."""
import argparse
import fcntl
import json
import logging
import math
import os
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import yaml

from agents import (APP_NAMES, MIB, AnalysisAgent, OOMPreference, ResponseAgent,
                    WatchAgent, get_process, priority)
from budget import Budget, BudgetExceeded
from model_pricing import rates_for
from history import initialize, record
from priorities import read_priorities
from response_status import explain
from tab_response import TabResponse
from build_info import BUILD
from memory_knowledge import retrieve
from memory_reclaim import ReclaimResponse
from early_pressure import EarlyPressure
from protection_client import publish, status as protection_status

LOG = logging.getLogger('probe')
STATE = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'probe'
RUNTIME = Path(os.environ.get('XDG_RUNTIME_DIR') or os.environ.get('XDG_CACHE_HOME')
               or Path.home() / '.cache') / 'probe'


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if path.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError(f'사용자 전용 폴더가 필요합니다: {path}')


def atomic_json(path, value):
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def overrides():
    return {k: v for k, v in read_priorities(STATE / 'priorities.json').items() if k in APP_NAMES or (k.startswith('process:') and len(k) == 28)}


class Skills:
    """Store local reclaim rules separately from non-executable termination evidence."""
    def __init__(self, database):
        self.database = database
        self.matches = {}
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE IF NOT EXISTS app_labels (id TEXT PRIMARY KEY, name TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS outcomes (app TEXT PRIMARY KEY, successes INTEGER, failures INTEGER, gain_mib REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS skills (scenario TEXT PRIMARY KEY, app TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS termination_records (scenario TEXT, app TEXT, PRIMARY KEY(scenario,app))')
            db.execute("INSERT OR IGNORE INTO termination_records SELECT scenario,app FROM skills WHERE app NOT IN ('reclaim_cgroup','chrome_tab_highest_priority')")
            db.execute("DELETE FROM skills WHERE app NOT IN ('reclaim_cgroup','chrome_tab_highest_priority')")

    def get(self, scenario, candidates):
        # App termination outcomes are evidence, never executable learned skills.
        return None

    def save(self, scenario, key):
        self.matches.pop(scenario, None)
        with sqlite3.connect(self.database) as db:
            if key in ('reclaim_cgroup','chrome_tab_highest_priority'):
                db.execute('INSERT OR REPLACE INTO skills VALUES (?,?)', (scenario, key))
            else:
                db.execute('INSERT OR IGNORE INTO termination_records VALUES (?,?)', (scenario,key))

    def record_outcome(self, key, success, gain):
        with sqlite3.connect(self.database) as db:
            db.execute('INSERT INTO outcomes VALUES (?,?,?,?) ON CONFLICT(app) DO UPDATE SET successes=successes+excluded.successes, failures=failures+excluded.failures, gain_mib=(gain_mib+excluded.gain_mib)/2',
                       (key,int(success),int(not success),max(0,gain)))

    def rank(self, candidates):
        with sqlite3.connect(self.database) as db:
            results={key:(ok,bad,gain) for key,ok,bad,gain in db.execute('SELECT * FROM outcomes')}
        def score(app):
            ok,bad,gain=results.get(app.key,(0,0,0))
            return (-(ok+1)/(ok+bad+2), -max(gain,app.rss/MIB*.1), -app.rss, app.key)
        return sorted(candidates,key=score)

    def count(self):
        with sqlite3.connect(self.database) as db:
            return db.execute('SELECT COUNT(*) FROM skills').fetchone()[0]

    def discard(self, scenario):
        with sqlite3.connect(self.database) as db:
            db.execute('DELETE FROM skills WHERE scenario IN (?,?)', (scenario, self.matches.pop(scenario, scenario)))


class Advisor:
    """One bounded, structured API call per unseen pressure episode."""
    def __init__(self, settings, budget):
        self.model = settings.get('model') or 'gpt-6-luna'
        self.key = settings.get('api_key') or os.environ.get('OPENAI_API_KEY', '')
        self.budget = budget

    @property
    def enabled(self):
        return bool(self.model and self.key and self.budget.limit and not getattr(self, 'failed', False))

    def advise(self, telemetry, candidates):
        from openai import OpenAI

        choices = [item['id'] for item in candidates]
        if not self.enabled or not choices:
            return None
        instructions = ('Choose one listed permitted memory-recovery action or low-priority application, '
                        'or choose none. Only the listed identifiers may be returned. '
                        'Use observed pressure and provided resource data; tab RSS may be unavailable. '
                        'You cannot execute actions or provide commands.')
        evidence = []
        with sqlite3.connect(self.budget.path) as db:
            try:
                rows = db.execute("SELECT kind, details FROM history WHERE kind IN ('skill_saved','recovery_failed','action_cancelled') ORDER BY id DESC LIMIT 12").fetchall()
                evidence = [{'outcome':kind, 'measurement':json.loads(details)} for kind,details in rows]
            except sqlite3.Error:
                pass
        payload = json.dumps({'history_evidence': evidence, 'memory': telemetry, 'eligible_apps': candidates, 'reference_policy': retrieve(candidates)}, ensure_ascii=False)
        schema = {'type': 'object', 'properties': {'app': {'type': 'string',
                   'enum': ['none', *choices]}}, 'required': ['app'], 'additionalProperties': False}
        input_rate, output_rate = rates_for(self.model)
        estimate = math.ceil((len(payload.encode()) + len(instructions.encode())
                              + len(json.dumps(schema)) + 4096) * input_rate + 300 * output_rate)
        reservation = self.budget.reserve(estimate)
        with OpenAI(api_key=self.key, max_retries=0, timeout=8) as client:
            response = client.responses.create(
                model=self.model, instructions=instructions, input=payload,
                max_output_tokens=300, store=False,
                text={'format': {'type': 'json_schema', 'name': 'probe_choice',
                                 'strict': True, 'schema': schema}})
        if response.usage is None:
            raise RuntimeError('API 사용량을 확인하지 못했습니다')
        usage = response.usage
        self.budget.finalize(reservation, math.ceil(usage.input_tokens * input_rate
                             + usage.output_tokens * output_rate), usage.input_tokens, usage.output_tokens)
        selected = json.loads(response.output_text)['app']
        return selected if selected in (*choices, 'none') else None


def notify(message):
    LOG.info('%s', message)
    try:
        subprocess.run(('notify-send', 'Memory Pressure Manager', message), timeout=2, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def scenario_for(sample, previous, candidates):
    if sample.full is None:
        pressure = 'psi-unavailable'
    else:
        pressure = 'stall' if sample.full >= 2 else 'low-available'
    swap_io = previous is not None and (sample.swap_in > previous.swap_in or sample.swap_out > previous.swap_out)
    return json.dumps([pressure, swap_io, sorted(app.key for app in candidates)])


def same_candidates(first, second):
    return json.loads(first)[2] == json.loads(second)[2]


def snapshot(sample):
    return {'available_mib': round(sample.available / MIB), 'total_mib': round(sample.total / MIB),
            'swap_used_mib': round(sample.swap_used / MIB), 'psi_some_avg10': sample.some,
            'psi_full_avg10': sample.full}


def recovery_verified(before, after, critical_percent, exited):
    gain = max(32 * MIB, int(before.total * .002))
    return (exited and after.available - before.available >= gain
            and (after.full is None or after.full <= (before.full or 0) + .5))


class Probe:
    def __init__(self, config, database):
        cfg = config.get('watch', {})
        self.interval = int(cfg.get('interval_seconds', 5))
        self.threshold = float(cfg.get('critical_available_percent', 8))
        confirm = int(cfg.get('consecutive_samples', 3))
        self.verify_seconds = int(cfg.get('verification_seconds', 25))
        self.cooldown = int(cfg.get('cooldown_seconds', 90))
        self.allow_force = bool(cfg.get('allow_force_kill', True))
        self.force_after = int(cfg.get('force_kill_after_seconds', 8))
        if not 2 <= self.force_after <= 60:
            raise ValueError('force_kill_after_seconds는 2~60초여야 합니다')
        if not (2 <= self.interval <= 60 and 3 <= self.threshold <= 20 and 2 <= confirm <= 10
                and self.interval <= self.verify_seconds <= 120 and self.cooldown >= 60):
            raise ValueError('watch 설정 범위를 확인하세요')
        self.database = database
        initialize(database)
        self.watch = WatchAgent()
        self.analysis = AnalysisAgent(self.threshold, confirm)
        self.early = EarlyPressure(config)
        self.protection = OOMPreference() if cfg.get('systemd_oom_preference', True) else None
        self.skills = Skills(database)
        ai = config.get('openai', {})
        self.budget = Budget(database, round(float(ai.get('monthly_budget_usd', 0)) * 1_000_000))
        self.advisor = Advisor(ai, self.budget)
        self.automatic_analysis = ai.get('automatic_analysis', False) is True
        self.stop = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.tab_response = TabResponse(self, RUNTIME)
        self.reclaim_response = ReclaimResponse(self, config)

    def event(self, kind, details):
        try:
            record(self.database, kind, details)
            if kind in ('skill_saved','action_effective','recovery_failed') and hasattr(self,'skills'):
                self.skills.record_outcome(details['app'], kind in ('skill_saved','action_effective'), details.get('after_mib',0)-details.get('before_mib',0))
        except (sqlite3.Error, OSError):
            LOG.warning("대응 이력을 저장하지 못했습니다")

    def ask_advisor(self, scenario, telemetry, candidates):
        self.event("ai_request", {"scenario": scenario, "memory": telemetry, "candidates": candidates, "model": self.advisor.model, "sources": [d["source"] for d in retrieve(candidates)]})
        try:
            choice = self.advisor.advise(telemetry, candidates)
        except Exception as exc:
            self.advisor.failed = True
            self.event("ai_error", {"scenario": scenario, "error": type(exc).__name__})
            raise
        self.planned_choice = choice
        self.event("ai_proposal", {"scenario": scenario, "app": choice, "model": self.advisor.model})
        return choice

    def run(self):
        previous = None
        next_inventory = 0
        next_action = 0
        next_notice = 0
        apps = []
        settings = {}
        trial = None
        advice = None
        used_advice = False
        declined_scenarios = set()
        unsuccessful_apps = set()
        summary = '메모리 감시 중'
        ai_disabled_reported = False
        ai_status = '자동 API 호출 꺼짐 · 로컬 대응 실행' if not self.automatic_analysis else '기록 분석 대기 · 대응은 로컬 실행' if self.advisor.enabled else 'API 키 또는 월 예산 확인'
        last_level = None
        postmortem = None
        while not self.stop.is_set():
            now = time.monotonic()
            sample = self.watch.sample()
            level = self.early.classify(sample, self.analysis.pressure(sample), now)
            emergency = sample.available_percent <= 3 or ((sample.full or 0) >= 10 and sample.available_percent < 8)
            if emergency:
                level = 'critical'
            if level == 'normal' and last_level == 'critical' and self.automatic_analysis and self.advisor.enabled and (postmortem is None or postmortem.done()):
                options = ResponseAgent.candidates(apps, settings)[:5]
                if options:
                    postmortem = self.executor.submit(self.ask_advisor, scenario_for(sample, previous, options), snapshot(sample), [{'id':a.key, 'rss_mib':round(a.rss/MIB)} for a in options])
            termination_allowed = level == 'critical' and sample.available_percent <= min(self.threshold, 8)
            if level != last_level and level != 'normal':
                self.event('pressure_' + level, {'memory': snapshot(sample)})
            last_level = level
            current_settings = overrides()
            if now >= next_inventory or current_settings != settings or emergency:
                apps = self.analysis.applications()
                with sqlite3.connect(self.database) as db:
                    db.executemany('INSERT OR REPLACE INTO app_labels VALUES (?,?)', [(a.key, a.name) for a in apps])
                next_inventory = now + (2 if level != 'normal' else 5)
                settings = current_settings
                if self.protection:
                    self.protection.sync(apps, settings)
            publish(RUNTIME / 'protection.json', apps, settings, atomic_json)
            if advice and advice[1].done() and not ResponseAgent.candidates(apps, settings):
                # A completed app request must not block tab handling after its app disappeared.
                advice = None
                used_advice = False
            if level == 'normal':
                if summary.startswith('메모리 압박 · '):
                    summary = '메모리 감시 중'
                used_advice = False
                declined_scenarios.clear()
                unsuccessful_apps.clear()
            if trial and not termination_allowed and not trial.get('forced'):
                trial['forced'] = True
                self.event('action_cancelled', {'app':trial['app'], 'display_name':trial['target'].name, 'reason':'force_cancelled_pressure_relieved'})
            if trial and self.allow_force and not trial.get('forced') and now >= trial['force_at']:
                trial['forced'] = True
                try:
                    alive = get_process(trial['process'].pid)
                    if alive and alive.started == trial['process'].started:
                        ResponseAgent.close(trial['target'], overrides(), force=True)
                        self.event('action_forced', {'app': trial['app'], 'display_name': trial['target'].name, 'source': trial['source']})
                        trial['verify_at'] = min(trial['verify_at'], now + max(self.interval, 5))
                except (OSError, ValueError, psutil.Error):
                    self.event('force_blocked', {'app': trial['app'], 'display_name': trial['target'].name})
                    self.event('action_cancelled', {'app':trial['app'], 'display_name':trial['target'].name})
                    trial = None
            if trial and sample.available - trial['before'].available >= 32*MIB:
                try:
                    exited_now = get_process(trial['process'].pid)
                    if not exited_now or exited_now.started != trial['process'].started:
                        trial['verify_at'] = now
                except psutil.NoSuchProcess:
                    trial['verify_at'] = now
                except (psutil.Error,OSError):
                    pass
            if trial and now >= trial['verify_at']:
                try:
                    proc = get_process(trial['process'].pid)
                    exited = not proc or proc.started != trial['process'].started
                except psutil.NoSuchProcess:
                    exited = True
                except (psutil.Error, OSError):
                    exited = False
                key = trial['scenario']
                if recovery_verified(trial['before'], sample, self.threshold, exited):
                    self.skills.save(key, trial['app'])
                    self.event('action_effective', {'scenario': key, 'app': trial['app'], 'source': trial['source'], 'display_name': trial.get('display_name'), 'before_mib': round(trial['before'].available / MIB), 'after_mib': round(sample.available / MIB)})
                    summary = '앱 종료 후 메모리 개선 관측 · 효과 기록 저장'
                    notify(summary)
                else:
                    self.skills.discard(key)
                    self.event('recovery_failed', {'scenario': key, 'app': trial['app'], 'source': trial['source'], 'display_name': trial.get('display_name'), 'before_mib': round(trial['before'].available / MIB), 'after_mib': round(sample.available / MIB)})
                    unsuccessful_apps.add(trial['app'])
                    summary = '메모리 회복 미확인 · 저장된 대응 해제'
                    used_advice = False
                    notify(summary)
                trial = None

            reclaim_busy = False
            if not trial and advice is None and not self.tab_response.trial and not self.tab_response.future:
                reclaim_busy = self.reclaim_response.tick(sample, level, now, apps, settings)
                if reclaim_busy:
                    summary = self.reclaim_response.summary
                    ai_status = '메모리 회수 · ' + summary
            tab_busy = reclaim_busy
            if not trial and not reclaim_busy and advice is None and now >= next_action and (self.early.active or level == 'critical' or self.tab_response.trial):
                tab_busy = self.tab_response.tick(sample, level, now)
                if tab_busy:
                    summary = self.tab_response.summary
                    ai_status = '탭 대응 · ' + summary
            if termination_allowed and not trial and not tab_busy and now >= next_action:
                candidates = [app for app in ResponseAgent.candidates(apps, settings)
                              if app.key not in unsuccessful_apps]
                if not candidates:
                    if now >= next_notice:
                        detail = explain(apps, settings, unsuccessful_apps, RUNTIME)
                        summary = '메모리 압박 · ' + detail['summary']
                        self.event('no_candidates', {'memory': snapshot(sample), 'blocked': detail['blocked'], 'tabs': detail['tabs']})
                        notify(summary)
                        next_notice = now + self.cooldown
                else:
                    best_priority = priority(candidates[0].key, settings)
                    candidates = [a for a in candidates if priority(a.key, settings) == best_priority]
                    candidates = self.skills.rank(candidates)
                    scenario = scenario_for(sample, previous, candidates)
                    app = self.skills.get(scenario, candidates)
                    source = 'skill' if app else None
                    if app is None:
                        app = next((a for a in candidates if a.key == getattr(self,'planned_choice',None)),None)
                        if app: source = 'plan'
                    if not self.advisor.enabled and not ai_disabled_reported:
                        reason = 'no_api_key' if not self.advisor.key else 'zero_budget'
                        self.event('ai_disabled', {'reason': reason})
                        ai_disabled_reported = True
                    declined = scenario in declined_scenarios
                    if not app and advice and advice[1].done():
                        asked_scenario, future = advice
                        advice = None
                        try:
                            choice = future.result()
                            # Pressure/swap readings can change while the API is in flight;
                            # only reuse a response if the permitted candidates still match.
                            if same_candidates(asked_scenario, scenario):
                                ai_status = 'AI 응답 수신'
                                declined = choice == 'none'
                                if declined:
                                    declined_scenarios.add(scenario)
                                app = next((a for a in candidates if a.key == choice), None)
                                if app:
                                    source = 'ai'
                            else:
                                used_advice = False
                                ai_status = '후보 변경 · AI 재분석 대기'
                        except BudgetExceeded:
                            ai_status = '월 예산으로 AI 호출 차단'
                            LOG.info('월 예산으로 API 분석 차단')
                        except Exception as exc:
                            ai_status = 'AI 오류: ' + type(exc).__name__
                            LOG.warning('API 분석을 완료하지 못함 (%s)', type(exc).__name__)
                    if emergency and not app:
                        advice = None
                        declined = False
                        used_advice = True
                        source = 'emergency'
                    if app or (not advice and not declined):
                        app = app or candidates[0]
                        source = source or 'local'
                        # Check the policy again immediately before signaling.
                        current = overrides()
                        fresh = [a for a in ResponseAgent.candidates(apps, current)
                                 if a.key not in unsuccessful_apps]
                        next_action = now + (2 if level == 'critical' else self.cooldown)
                        if fresh and priority(app.key, current) == priority(fresh[0].key, current):
                            try:
                                ResponseAgent.close(app, current)
                            except (ValueError, OSError, psutil.Error) as exc:
                                self.skills.discard(scenario)
                                self.event('action_failed', {'scenario': scenario, 'app': app.key, 'source': source, 'error': type(exc).__name__})
                                unsuccessful_apps.add(app.key)
                                LOG.info('앱 종료 요청 보류 (%s)', type(exc).__name__)
                            else:
                                self.event('action_requested', {'scenario': scenario, 'app': app.key, 'source': source, 'display_name': app.name})
                                summary = app.name + ' 종료 요청 · 메모리 회복 확인 중'
                                notify(summary)
                                trial = {'scenario': scenario, 'app': app.key, 'process': app.leaders[0],
                                         'before': sample, 'source': source, 'display_name': app.name, 'target': app, 'forced': False,
                                         'force_at': now + (2 if emergency else self.force_after), 'verify_at': now + (6 if emergency else max(self.force_after + 2, min(self.verify_seconds, 6)))}
                    elif declined:
                        next_action = now + (2 if level == 'critical' else self.cooldown)
                        summary = '현재 대응 후보의 종료 보류'
            elif level != 'critical' and advice and advice[1].done():
                advice = None

            guard = protection_status()
            protected_pids = set(guard.get('protected_pids', []))
            atomic_json(RUNTIME / 'status.json', {
                **snapshot(sample), 'usage': round(100 - sample.available_percent), 'level': level,
                'kernel_protection': guard, 'timestamp': time.time(), 'build': BUILD, 'source_dir': str(Path(__file__).resolve().parent), 'pid': os.getpid(), 'cost_micro': self.budget.monthly()['estimated_microusd'],
                'termination_allowed': termination_allowed, 'discard_allowed': self.early.active or level == 'critical',
                'optimization_status': self.reclaim_response.capability, 'status': summary, 'ai_status': ai_status, 'apps': [{'id': a.key, 'name': a.name, 'rss_mib': round(a.rss / MIB),
                                          'kernel_pid_count': sum(p.pid in protected_pids for p in a.processes), 'process_count':len(a.processes),
                                          'blocked_reason': ResponseAgent.blocked_reason(a, settings, unsuccessful_apps),
                                          'eligible': ResponseAgent.blocked_reason(a, settings, unsuccessful_apps) is None, 'priority': priority(a.key, settings) if a.managed else 0}
                                         for a in apps],
                'protected_scopes': len(self.protection.applied) if self.protection else 0,
                'learned_skills': self.skills.count(),
            })
            previous = sample
            self.stop.wait(min(self.interval, 1 if level != 'normal' else 2))

    def close(self):
        self.stop.set()
        (RUNTIME / 'status.json').unlink(missing_ok=True)
        (RUNTIME / 'protection.json').unlink(missing_ok=True)
        if self.protection:
            self.protection.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='config.yml')
    parser.add_argument('--diagnose', action='store_true', help='민감정보 없이 설정 및 대응 조건 확인 (API 호출 없음)')
    parser.add_argument('--check-ai', action='store_true', help='예산 안에서 소액 OpenAI 테스트 호출; 앱 종료 없음')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    os.umask(0o077)
    private_directory(STATE)
    private_directory(RUNTIME)
    path = Path(args.config).expanduser()
    config = {}
    if path.exists():
        with path.open() as file:
            st = os.fstat(file.fileno())
            if st.st_uid != os.getuid() or st.st_mode & 0o077:
                parser.error('설정 파일 권한을 chmod 600으로 바꾸세요')
            config = yaml.safe_load(file) or {}
    if args.check_ai:
        ai = config.get('openai', {})
        budget = Budget(STATE / 'probe.sqlite3', round(float(ai.get('monthly_budget_usd', 0)) * 1_000_000))
        advisor = Advisor(ai, budget)
        if not advisor.enabled:
            print('AI 테스트 불가: API 키와 0보다 큰 월 예산을 확인하세요.')
            raise SystemExit(1)
        before = budget.monthly()
        initialize(STATE / 'probe.sqlite3')
        try:
            result = advisor.advise({'available_mib': 1024, 'total_mib': 16384},
                                    [{'id': 'test_app', 'rss_mib': 256}])
            if result not in ('none', 'test_app'):
                raise ValueError('AI 응답 형식 오류')
        except Exception as exc:
            record(STATE / 'probe.sqlite3', 'ai_test_failed', {'model': advisor.model, 'error': type(exc).__name__})
            print('AI 테스트 실패:', type(exc).__name__)
            print('키 값은 출력하지 않습니다. 위 오류 유형과 서비스 로그를 확인하세요.')
            raise SystemExit(1)
        after = budget.monthly()
        record(STATE / 'probe.sqlite3', 'ai_test_success', {'model': advisor.model})
        print('AI API 테스트 성공 · 모델:', advisor.model)
        print('테스트 응답:', result, '· 앱 종료 없음')
        print('이번 달 로컬 추정 사용액: $%.6f (테스트 전 $%.6f)' %
              (after['estimated_microusd'] / 1e6, before['estimated_microusd'] / 1e6))
        print('실제 청구액은 로컬 추정치와 다를 수 있습니다.')
        return
    if args.diagnose:
        from diagnostics import inspect
        result = inspect(config, overrides(), STATE / 'probe.sqlite3')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    with (RUNTIME / 'guard.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('Memory Pressure Manager가 이미 실행 중입니다')
        guard = Probe(config, STATE / 'probe.sqlite3')
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: guard.stop.set())
        try:
            guard.run()
        finally:
            guard.close()


if __name__ == '__main__':
    main()
