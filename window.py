#!/usr/bin/python3
"""GTK view and local priorities editor. Uses Ubuntu's GTK instead of the venv."""
import json
import os
import sqlite3
from datetime import datetime
import tempfile
import time
from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk
from history import read_history
from priorities import read_priorities

STATE = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'probe'
RUNTIME = Path(os.environ.get('XDG_RUNTIME_DIR') or os.environ.get('XDG_CACHE_HOME')
               or Path.home() / '.cache') / 'probe'
CSS = b'''
window { background: #000; color: #fff; }
.title { font-size: 22px; font-weight: 700; }
.eyebrow { font-size: 12px; font-weight: 700; color: #b0b0b0; }
.muted { color: #bcbcbc; }
.frame { background: #111; border: 1px solid #303030; border-radius: 12px; padding: 14px; }
viewport, list { background: #000; }
row { border-bottom: 1px solid #252525; }
combobox button { color: #fff; background: #171717; background-image: none;
 border: 1px solid #404040; box-shadow: none; border-radius: 7px; padding: 5px 8px; }
combobox button:hover { background: #292929; }
.history-button { color: #fff; background: #171717; background-image: none; border: 1px solid #555; border-radius: 9px; padding: 10px; box-shadow: none; }
.history-button:hover { background: #292929; }
'''


def label(value='', style=None):
    widget = Gtk.Label(label=value, xalign=0)
    if style:
        widget.get_style_context().add_class(style)
    return widget


def read(path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def update_priority(key, value):
    path = STATE / 'priorities.json'
    values = read_priorities(path)
    values[key] = value
    fd, tmp = tempfile.mkstemp(dir=STATE)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump({'version': 2, 'apps': values}, f)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


APP_NAMES = {'idea': 'IntelliJ IDEA', 'code': 'VS Code', 'datagrip': 'DataGrip',
             'chrome': 'Google Chrome', 'chromium': 'Chromium', 'firefox': 'Firefox',
             'slack': 'Slack', 'discord': 'Discord', 'edge': 'Microsoft Edge',
             'codium': 'VSCodium', 'pycharm': 'PyCharm', 'webstorm': 'WebStorm'}


def app_name(key):
    return APP_NAMES.get(key, key or '없음')


def scenario_text(value):
    try:
        pressure, swap, candidates = json.loads(value)
        pressure_text = {'stall': '메모리 압박', 'low-available': '가용 메모리 부족',
                         'psi-unavailable': '압박 지표 확인 불가'}.get(pressure, pressure)
        return f"{pressure_text} · 스왑 I/O {'있음' if swap else '없음'} · 후보 {', '.join(app_name(x) for x in candidates)}"
    except (ValueError, TypeError):
        return '상황 정보 없음'


def event_text(kind, details):
    app = app_name(details.get('app'))
    source = {'skill': '저장된 스킬', 'ai': 'AI 제안', 'local': '로컬 기본 판단'}.get(details.get('source'), '')
    titles = {'ai_request': 'AI 분석 요청', 'ai_proposal': 'AI 제안', 'ai_error': 'AI 분석 실패',
              'skill_saved': '스킬 저장 / 회복 확인', 'recovery_failed': '회복 미확인 / 스킬 해제',
              'action_requested': '앱 종료 요청', 'action_failed': '앱 종료 요청 실패',
              'no_candidates': '자동 종료 가능한 앱 없음', 'ai_disabled': 'AI 분석 사용 불가'}
    lines = []
    if kind == 'ai_request':
        memory = details.get('memory', {})
        lines.append(f"모델 {details.get('model', '')} · 가용 {memory.get('available_mib', '?')} MiB · 스왑 {memory.get('swap_used_mib', '?')} MiB")
        lines.append('후보: ' + ', '.join(f"{app_name(x.get('id'))} ({x.get('rss_mib', '?')} MiB)" for x in details.get('candidates', [])))
    elif kind == 'ai_proposal':
        lines.append('제안: ' + ('종료 보류' if details.get('app') == 'none' else app))
    elif kind == 'no_candidates':
        lines.append('종료 허용 우선순위, 앱 인식 여부와 단일 실행 여부를 확인하세요.')
    elif kind == 'ai_disabled':
        lines.append({'no_api_key': 'API 키가 설정되지 않았습니다.', 'zero_budget': '월 예산이 0입니다.'}.get(details.get('reason'), '설정을 확인하세요.'))
    elif kind == 'ai_error':
        lines.append('오류 유형: ' + details.get('error', '알 수 없음'))
    else:
        lines.append(f"대상: {app}" + (f" · 판단: {source}" if source else ''))
        if 'before_mib' in details:
            lines.append(f"가용 메모리: {details['before_mib']} → {details['after_mib']} MiB")
        if 'error' in details:
            lines.append('오류 유형: ' + details['error'])
    if details.get('scenario'):
        lines.append(scenario_text(details['scenario']))
    return titles.get(kind, kind), '\n'.join(lines)


class HistoryWindow(Gtk.Window):
    def __init__(self, parent):
        super().__init__(title='Probe · 대응 기록', transient_for=parent)
        self.set_default_size(650, 650)
        self.set_border_width(22)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(root)
        root.pack_start(label('PROBE', 'eyebrow'), False, False, 0)
        root.pack_start(label('AI 제안과 대응 기록', 'title'), False, False, 0)
        note = label('기록은 이 기기에만 저장됩니다. 최근 2,000건까지 보관하고 최신 200건을 표시합니다.', 'muted')
        note.set_line_wrap(True)
        root.pack_start(note, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        scroll.add(self.content)
        root.pack_start(scroll, True, True, 0)
        refresh = Gtk.Button(label='새로고침')
        refresh.get_style_context().add_class('history-button')
        refresh.connect('clicked', lambda *_: self.refresh())
        root.pack_start(refresh, False, False, 0)
        self.refresh()

    def card(self, title, body):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.get_style_context().add_class('frame')
        box.pack_start(label(title), False, False, 0)
        text = label(body, 'muted')
        text.set_line_wrap(True)
        text.set_selectable(True)
        box.pack_start(text, False, False, 0)
        self.content.pack_start(box, False, False, 0)

    def refresh(self):
        for child in self.content.get_children():
            self.content.remove(child)
        try:
            skills, events = read_history(STATE / 'probe.sqlite3')
            self.content.pack_start(label(f'현재 저장된 스킬 · {len(skills)}개', 'eyebrow'), False, False, 0)
            if not skills:
                self.card('저장된 스킬 없음', '대응 후 메모리 회복이 확인되면 저장됩니다.')
            for scenario, app in skills:
                self.card(app_name(app) + ' 종료 요청', scenario_text(scenario))
            self.content.pack_start(label('최근 활동', 'eyebrow'), False, False, 0)
            if not events:
                self.card('아직 기록이 없습니다', '이 업데이트 이후 발생하는 AI 제안과 대응부터 기록됩니다.')
            for timestamp, kind, raw in events:
                try:
                    title, body = event_text(kind, json.loads(raw))
                    when = datetime.fromtimestamp(timestamp).strftime('%Y.%m.%d %H:%M:%S')
                    self.card(f'{title}  ·  {when}', body)
                except (ValueError, TypeError, KeyError, OverflowError):
                    continue
        except (sqlite3.Error, OSError):
            self.card('기록을 불러올 수 없습니다', 'Probe가 실행 중인지 확인한 뒤 다시 시도하세요.')
        self.content.show_all()


class Window(Gtk.Window):
    def __init__(self):
        super().__init__(title='Probe')
        self.set_default_size(650, 620)
        self.set_border_width(22)
        self.connect('destroy', Gtk.main_quit)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.add(root)
        root.pack_start(label('PROBE', 'eyebrow'), False, False, 0)
        root.pack_start(label('앱 보호와 종료 순서', 'title'), False, False, 0)
        summary = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        summary.get_style_context().add_class('frame')
        self.metrics = label()
        self.note = label(style='muted')
        self.note.set_line_wrap(True)
        summary.pack_start(self.metrics, False, False, 0)
        summary.pack_start(self.note, False, False, 0)
        root.pack_start(summary, False, False, 0)
        guide = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        guide.get_style_context().add_class('frame')
        for text in ('보호 · Probe가 종료하지 않으며 시스템 보호를 시도합니다.',
                     '우선순위 1~999 · 숫자가 낮을수록 중요합니다. 숫자가 큰 앱부터 종료를 요청합니다.',
                     '같은 숫자는 허용됩니다. 운영체제의 강제 종료까지 막을 수는 없습니다.'):
            item = label(text, 'muted');item.set_line_wrap(True);guide.pack_start(item, False, False, 0)
        root.pack_start(guide, False, False, 0)
        root.pack_start(label('메모리를 사용하는 앱', 'eyebrow'), False, False, 0)
        self.rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        scroll = Gtk.ScrolledWindow()
        scroll.add(self.rows)
        root.pack_start(scroll, True, True, 0)
        caption = label('자동 종료 요청은 저장하지 않은 작업을 잃게 할 수 있습니다.', 'muted')
        caption.set_line_wrap(True)
        root.pack_start(caption, False, False, 0)
        history_button = Gtk.Button(label='AI 제안 · 스킬 · 대응 기록 보기')
        history_button.get_style_context().add_class('history-button')
        history_button.connect('clicked', self.show_history)
        root.pack_start(history_button, False, False, 0)
        self.history_window = None
        self.entries = {}
        self.render()
        GLib.timeout_add_seconds(3, self.render)

    def show_history(self, *_):
        if self.history_window is None:
            self.history_window = HistoryWindow(self)
            self.history_window.connect('destroy', self.history_closed)
            self.history_window.show_all()
        else:
            self.history_window.refresh()
            self.history_window.present()

    def history_closed(self, *_):
        self.history_window = None

    def changed(self, widget, key):
        row, memory, protect, number = self.entries[key]
        number.set_sensitive(not protect.get_active())
        try:
            update_priority(key, 0 if protect.get_active() else number.get_value_as_int())
        except OSError:
            self.note.set_text('우선순위를 저장할 수 없습니다. Probe가 실행 중인지 확인하세요.')

    def add_app(self, item):
        key = item['id']
        row = Gtk.ListBoxRow()
        line = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8)
        row.add(line)
        line.pack_start(label(item['name']), True, True, 0)
        memory = label(style='muted')
        line.pack_start(memory, False, False, 0)
        protect = Gtk.CheckButton(label='보호')
        number = Gtk.SpinButton.new_with_range(1, 999, 1)
        number.set_numeric(True)
        number.set_width_chars(4)
        value = item['priority']
        protect.set_active(value == 0)
        number.set_value(value if value else 500)
        number.set_sensitive(value != 0)
        line.pack_end(number, False, False, 0)
        line.pack_end(protect, False, False, 0)
        self.rows.add(row)
        self.entries[key] = (row, memory, protect, number)
        protect.connect('toggled', self.changed, key)
        number.connect('value-changed', self.changed, key)
        row.show_all()

    def render(self):
        data = read(RUNTIME / 'status.json')
        if time.time() - data.get('timestamp', 0) > 75:
            self.metrics.set_text('Probe 감시가 실행 중이 아닙니다')
            self.note.set_text('')
            data = {}
        else:
            self.metrics.set_text(f"RAM {data['usage']}%  ·  가용 {data['available_mib'] / 1024:.1f} GiB"
                                  f"  ·  이번 달 ${data['cost_micro'] / 1e6:.4f}")
            self.note.set_text(f"{data['status']}  ·  저장된 대응 {data['learned_skills']}개"
                               f"  ·  systemd 보호 적용 {data['protected_scopes']}개")
        saved = read_priorities(STATE / 'priorities.json')
        items = sorted(data.get('apps', []), key=lambda item: (-item['rss_mib'], item['name']))[:12]
        current = {item['id'] for item in items}
        for key in self.entries.keys() - current:
            self.rows.remove(self.entries.pop(key)[0])
        for item in items:
            key = item['id']
            if key not in self.entries:
                self.add_app(item)
            row, memory, protect, number = self.entries[key]
            memory.set_text(f"약 {item['rss_mib']:,} MiB")
            # Do not overwrite edits or emit writes while refreshing.
            if not number.has_focus() and not protect.has_focus():
                value = saved.get(key, item['priority'])
                protect.handler_block_by_func(self.changed)
                number.handler_block_by_func(self.changed)
                protect.set_active(value == 0)
                if value:
                    number.set_value(value)
                number.set_sensitive(value != 0)
                protect.handler_unblock_by_func(self.changed)
                number.handler_unblock_by_func(self.changed)
            number.set_tooltip_text('숫자가 클수록 먼저 종료를 요청합니다.'
                                    if item['eligible'] else '단일 앱 프로세스를 확인할 수 없어 자동 종료 대상에서 제외됩니다.')
        return True


if __name__ == '__main__':
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    window = Window()
    window.show_all()
    Gtk.main()
