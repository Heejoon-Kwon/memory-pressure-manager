"""Read-only Probe diagnostics; never prints credentials or calls the OpenAI API."""
import json
import sqlite3
from pathlib import Path
from build_info import BUILD

from agents import AnalysisAgent, MIB, ResponseAgent, WatchAgent, priority
from model_pricing import rates_for, PricingUnavailable


def inspect(config, priorities, database):
    settings = config.get('openai', {})
    watch = config.get('watch', {})
    model = settings.get('model') or 'gpt-6-luna'
    apps = AnalysisAgent.applications()
    candidates = ResponseAgent.candidates(apps, priorities)
    sample = WatchAgent().sample()
    from memory_reclaim import target_for
    reclaimable = sum(target_for(a, priorities) is not None for a in apps)
    result = {
        'installed_build': BUILD,
        'diagnostic_source': str(Path(__file__).resolve().parent),
        'api_key_configured': bool(settings.get('api_key') or __import__('os').environ.get('OPENAI_API_KEY')),
        'model': model,
        'writable_reclaim_app_scopes': reclaimable,
        'monthly_budget_usd': settings.get('monthly_budget_usd', 0),
        'threshold_available_percent': watch.get('critical_available_percent', 8),
        'consecutive_samples': watch.get('consecutive_samples', 3),
        'interval_seconds': watch.get('interval_seconds', 5),
        'memory_available_percent_now': round(sample.available_percent, 1),
        'memory_psi_some_avg10': sample.some,
        'memory_psi_full_avg10': sample.full,
        'memory_mechanisms': {
            'psi_available': Path('/proc/pressure/memory').is_file(),
            'zram_devices': [p.name for p in Path('/sys/block').glob('zram*')],
            'cgroup_v2_available': Path('/sys/fs/cgroup/cgroup.controllers').is_file(),
        },
        'detected_apps': [{'name': a.name, 'memory_mib': round(a.rss / MIB),
                           'priority': priority(a.key, priorities),
                           'leader_count': len(a.leaders), 'eligible': a in candidates,
                           'blocked_reason': ResponseAgent.blocked_reason(a, priorities)} for a in apps],
        'skills_count': None, 'recent_events': [],
    }
    runtime = Path(__import__('os').environ.get('XDG_RUNTIME_DIR') or __import__('os').environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'probe'
    try:
        state = json.loads((runtime / 'status.json').read_text())
        result['running_instance'] = {k: state.get(k) for k in ('build', 'source_dir', 'pid', 'timestamp', 'status', 'ai_status')}
        result['running_build_matches'] = state.get('build') == BUILD
    except (ValueError, OSError):
        result['running_instance'] = None
    from chrome_tabs import snapshots
    profiles = snapshots(runtime)
    result['chrome_profiles'] = [{'build': p.get('build'), 'tabs': len(p['tabs']), 'web_apps': sum(t.get('standalone', False) for t in p['tabs'])} for p in profiles]
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
