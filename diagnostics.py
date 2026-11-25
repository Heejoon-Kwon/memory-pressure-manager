"""Read-only Probe diagnostics; never prints credentials or calls the OpenAI API."""
import json
import sqlite3
from pathlib import Path

from agents import AnalysisAgent, MIB, ResponseAgent, WatchAgent, priority
from model_pricing import rates_for, PricingUnavailable


def inspect(config, priorities, database):
    settings = config.get('openai', {})
    watch = config.get('watch', {})
    model = settings.get('model') or 'gpt-6-luna'
    apps = AnalysisAgent.applications()
    candidates = ResponseAgent.candidates(apps, priorities)
    sample = WatchAgent().sample()
    result = {
        'api_key_configured': bool(settings.get('api_key') or __import__('os').environ.get('OPENAI_API_KEY')),
        'model': model,
        'monthly_budget_usd': settings.get('monthly_budget_usd', 0),
        'threshold_available_percent': watch.get('critical_available_percent', 8),
        'consecutive_samples': watch.get('consecutive_samples', 3),
        'interval_seconds': watch.get('interval_seconds', 5),
        'memory_available_percent_now': round(sample.available_percent, 1),
        'memory_psi_some_avg10': sample.some,
        'memory_psi_full_avg10': sample.full,
        'detected_apps': [{'name': a.name, 'memory_mib': round(a.rss / MIB),
                           'priority': priority(a.key, priorities),
                           'leader_count': len(a.leaders), 'eligible': a in candidates} for a in apps],
        'skills_count': None, 'recent_events': [],
    }
    try:
        rates_for(model)
        result['model_pricing'] = 'available'
    except PricingUnavailable as exc:
        result['model_pricing'] = str(exc)
    if Path(database).is_file():
        try:
            with sqlite3.connect(f'{Path(database).resolve().as_uri()}?mode=ro', uri=True, timeout=3) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'skills' in tables:
                    result['skills_count'] = db.execute('SELECT COUNT(*) FROM skills').fetchone()[0]
                if 'history' in tables:
                    rows = db.execute('SELECT datetime(timestamp, \'unixepoch\', \'localtime\'), kind, details FROM history ORDER BY id DESC LIMIT 12').fetchall()
                    for when, kind, raw in rows:
                        try:
                            d = json.loads(raw)
                        except (ValueError, TypeError):
                            d = {}
                        # Only expose bounded labels, not prompts, credentials, or exception text.
                        result['recent_events'].append({'time': when, 'event': kind,
                                                        'error_type': d.get('error'), 'source': d.get('source')})
        except sqlite3.Error:
            result['database_error'] = '이력 데이터베이스를 읽을 수 없습니다'
    return result
