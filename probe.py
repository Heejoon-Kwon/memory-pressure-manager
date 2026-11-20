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
    try:
        data = json.loads((STATE / 'priorities.json').read_text())
    except (ValueError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in APP_NAMES and type(v) is int and 0 <= v <= 4}


class Skills:
    """Only observed successes are saved. Failed entries are removed."""
    def __init__(self, database):
        self.database = database
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE IF NOT EXISTS skills (scenario TEXT PRIMARY KEY, app TEXT NOT NULL)')

    def get(self, scenario, candidates):
        with sqlite3.connect(self.database) as db:
            row = db.execute('SELECT app FROM skills WHERE scenario=?', (scenario,)).fetchone()
        return next((app for app in candidates if row and app.key == row[0]), None)

    def save(self, scenario, key):
        with sqlite3.connect(self.database) as db:
            db.execute('INSERT OR REPLACE INTO skills VALUES (?,?)', (scenario, key))

    def count(self):
        with sqlite3.connect(self.database) as db:
            return db.execute('SELECT COUNT(*) FROM skills').fetchone()[0]

    def discard(self, scenario):
        with sqlite3.connect(self.database) as db:
            db.execute('DELETE FROM skills WHERE scenario=?', (scenario,))


class Advisor:
    """One bounded, structured API call per unseen pressure episode."""
    def __init__(self, settings, budget):
        self.model = settings.get('model') or 'gpt-6-luna'
        self.key = settings.get('api_key') or os.environ.get('OPENAI_API_KEY', '')
        self.budget = budget

    @property
    def enabled(self):
        return bool(self.model and self.key and self.budget.limit)

    def advise(self, telemetry, candidates):
        from openai import OpenAI

        choices = [item['id'] for item in candidates]
        if not self.enabled or not choices:
            return None
        instructions = ('Choose one listed low-priority application to close cleanly to recover memory, '
                        'or choose none. Only the listed identifiers may be returned. '
                        'Use observed memory pressure and estimated RSS to decide. '
                        'You cannot execute actions or provide commands.')
        payload = json.dumps({'memory': telemetry, 'eligible_apps': candidates}, ensure_ascii=False)
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
        subprocess.run(('notify-send', 'Probe', message), timeout=2, check=False,
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


def snapshot(sample):
    return {'available_mib': round(sample.available / MIB), 'total_mib': round(sample.total / MIB),
            'swap_used_mib': round(sample.swap_used / MIB), 'psi_some_avg10': sample.some,
            'psi_full_avg10': sample.full}


def recovery_verified(before, after, critical_percent, exited):
    gain = max(128 * MIB, int(before.total * .01))
    return (exited and after.available - before.available >= gain
            and after.available_percent >= critical_percent + 2
            and (after.full is None or after.full < 2 or after.full <= (before.full or 0) / 2))


class Probe:
    def __init__(self, config, database):
        cfg = config.get('watch', {})
        self.interval = int(cfg.get('interval_seconds', 5))
        self.threshold = float(cfg.get('critical_available_percent', 8))
        confirm = int(cfg.get('consecutive_samples', 3))
        self.verify_seconds = int(cfg.get('verification_seconds', 25))
        self.cooldown = int(cfg.get('cooldown_seconds', 90))
        if not (2 <= self.interval <= 60 and 3 <= self.threshold <= 20 and 2 <= confirm <= 10
                and self.interval <= self.verify_seconds <= 120 and self.cooldown >= 60):
            raise ValueError('watch 설정 범위를 확인하세요')
        self.database = database
        initialize(database)
        self.watch = WatchAgent()
        self.analysis = AnalysisAgent(self.threshold, confirm)
        self.protection = OOMPreference() if cfg.get('systemd_oom_preference', True) else None
        self.skills = Skills(database)
        ai = config.get('openai', {})
        self.budget = Budget(database, round(float(ai.get('monthly_budget_usd', 0)) * 1_000_000))
        self.advisor = Advisor(ai, self.budget)
        self.stop = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1)

    def event(self, kind, details):
        try:
            record(self.database, kind, details)
        except (sqlite3.Error, OSError):
            LOG.warning("대응 이력을 저장하지 못했습니다")

    def ask_advisor(self, scenario, telemetry, candidates):
        self.event("ai_request", {"scenario": scenario, "memory": telemetry, "candidates": candidates, "model": self.advisor.model})
        try:
            choice = self.advisor.advise(telemetry, candidates)
        except Exception as exc:
            self.event("ai_error", {"scenario": scenario, "error": type(exc).__name__})
            raise
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
        while not self.stop.is_set():
            now = time.monotonic()
            sample = self.watch.sample()
            level = self.analysis.pressure(sample)
            current_settings = overrides()
            if now >= next_inventory or current_settings != settings:
                apps = self.analysis.applications()
                next_inventory = now + 15
                settings = current_settings
                if self.protection:
                    self.protection.sync(apps, settings)
            if level == 'normal':
                used_advice = False
                declined_scenarios.clear()
                unsuccessful_apps.clear()
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
                    self.event('skill_saved', {'scenario': key, 'app': trial['app'], 'source': trial['source'], 'before_mib': round(trial['before'].available / MIB), 'after_mib': round(sample.available / MIB)})
                    summary = '가용 메모리 회복 확인 · 대응 기록 저장'
                    notify(summary)
                else:
                    self.skills.discard(key)
                    self.event('recovery_failed', {'scenario': key, 'app': trial['app'], 'source': trial['source'], 'before_mib': round(trial['before'].available / MIB), 'after_mib': round(sample.available / MIB)})
                    unsuccessful_apps.add(trial['app'])
                    summary = '메모리 회복 미확인 · 저장된 대응 해제'
                    used_advice = False
                    notify(summary)
                trial = None

            if level == 'critical' and not trial and now >= next_action:
                candidates = [app for app in ResponseAgent.candidates(apps, settings)
                              if app.key not in unsuccessful_apps]
                if not candidates:
                    if now >= next_notice:
                        summary = '메모리 압박 · 종료를 허용한 앱 없음'
                        notify(summary)
                        next_notice = now + self.cooldown
                else:
                    best_priority = priority(candidates[0].key, settings)
                    candidates = [a for a in candidates if priority(a.key, settings) == best_priority]
                    scenario = scenario_for(sample, previous, candidates)
                    app = self.skills.get(scenario, candidates)
                    source = 'skill' if app else None
                    declined = scenario in declined_scenarios
                    if not app and advice and advice[1].done():
                        asked_scenario, future = advice
                        advice = None
                        try:
                            choice = future.result()
                            if asked_scenario == scenario:
                                declined = choice == 'none'
                                if declined:
                                    declined_scenarios.add(scenario)
                                app = next((a for a in candidates if a.key == choice), None)
                                if app:
                                    source = 'ai'
                        except BudgetExceeded:
                            LOG.info('월 예산으로 API 분석 차단')
                        except Exception as exc:
                            LOG.warning('API 분석을 완료하지 못함 (%s)', type(exc).__name__)
                    if not app and not declined and not advice and not used_advice and self.advisor.enabled:
                        used_advice = True
                        payload = [{'id': a.key, 'rss_mib': round(a.rss / MIB)} for a in candidates]
                        advice = (scenario, self.executor.submit(self.ask_advisor, scenario, snapshot(sample), payload))
                        summary = '메모리 압박 분석 중'
                    elif app or (not advice and not declined):
                        app = app or candidates[0]
                        source = source or 'local'
                        # Check the policy again immediately before signaling.
                        current = overrides()
                        fresh = [a for a in ResponseAgent.candidates(apps, current)
                                 if a.key not in unsuccessful_apps]
                        next_action = now + self.cooldown
                        if fresh and priority(app.key, current) == priority(fresh[0].key, current):
                            try:
                                ResponseAgent.close(app, current)
                            except (ValueError, OSError, psutil.Error) as exc:
                                self.skills.discard(scenario)
                                self.event('action_failed', {'scenario': scenario, 'app': app.key, 'source': source, 'error': type(exc).__name__})
                                unsuccessful_apps.add(app.key)
                                LOG.info('앱 종료 요청 보류 (%s)', type(exc).__name__)
                            else:
                                self.event('action_requested', {'scenario': scenario, 'app': app.key, 'source': source})
                                summary = app.name + ' 종료 요청 · 메모리 회복 확인 중'
                                notify(summary)
                                trial = {'scenario': scenario, 'app': app.key, 'process': app.leaders[0],
                                         'before': sample, 'source': source, 'verify_at': now + self.verify_seconds}
                    elif declined:
                        next_action = now + self.cooldown
                        summary = '현재 대응 후보의 종료 보류'
            elif level != 'critical' and advice and advice[1].done():
                advice = None

            atomic_json(RUNTIME / 'status.json', {
                **snapshot(sample), 'usage': round(100 - sample.available_percent), 'level': level,
                'timestamp': time.time(), 'cost_micro': self.budget.monthly()['estimated_microusd'],
                'status': summary, 'apps': [{'id': a.key, 'name': a.name, 'rss_mib': round(a.rss / MIB),
                                          'eligible': len(a.leaders) == 1, 'priority': priority(a.key, settings)}
                                         for a in apps],
                'protected_scopes': len(self.protection.applied) if self.protection else 0,
                'learned_skills': self.skills.count(),
            })
            previous = sample
            self.stop.wait(self.interval)

    def close(self):
        self.stop.set()
        (RUNTIME / 'status.json').unlink(missing_ok=True)
        if self.protection:
            self.protection.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='config.yml')
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
    with (RUNTIME / 'guard.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('Probe가 이미 실행 중입니다')
        guard = Probe(config, STATE / 'probe.sqlite3')
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: guard.stop.set())
        try:
            guard.run()
        finally:
            guard.close()


if __name__ == '__main__':
    main()
