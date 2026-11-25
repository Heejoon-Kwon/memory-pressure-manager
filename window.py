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
from gi.repository import Gdk, GLib, Gtk, Gio, GdkPixbuf, Pango
import base64
from build_info import BUILD
from history import read_history, read_labels
from priorities import read_priorities
from chrome_tabs import snapshots, enqueue
from response_labels import REASONS

STATE = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'probe'
RUNTIME = Path(os.environ.get('XDG_RUNTIME_DIR') or os.environ.get('XDG_CACHE_HOME')
               or Path.home() / '.cache') / 'probe'
CSS = b'''
window { background: #000; color: #f5f5f5; font-family: sans-serif; }
headerbar { background: #000; background-image: none; border: none; box-shadow: none; color: #eee; }
headerbar button { background: transparent; background-image: none; border: none; box-shadow: none; }
.title { font-size: 22px; font-weight: 700; }
.eyebrow { font-size: 11px; font-weight: 700; color: #999; letter-spacing: 1px; }
.muted { color: #929292; font-size: 11px; }
.metric { font-size: 42px; font-weight: 700; letter-spacing: -1px; }
.stat { font-size: 14px; font-weight: 600; }
.frame { background: #0d0d0d; border: 1px solid #292929; border-radius: 12px; padding: 12px; }
viewport, list, scrolledwindow { background: #000; }
row { background: #101010; border: 1px solid #292929; border-radius: 12px; margin-bottom: 8px; padding: 12px; }
row:hover { background: #151515; }
.app-name { color: #f5f5f5; font-size: 13px; font-weight: 600; }
.memory { color: #f5f5f5; font-size: 13px; font-weight: 600; }
.badge { font-size: 11px; color: #ddd; background: #202020; padding: 4px 8px; border-radius: 10px; }
button { background: #101010; background-image: none; color: #ddd; border: 1px solid #383838; border-radius: 7px; box-shadow: none; }
button:hover { background: #242424; }
button:checked { color: #000; background: #eee; border-color: #eee; }
.filter button { padding: 5px 15px; border-radius: 7px; }
.protect { padding: 4px 9px; font-size: 11px; }
spinbutton { background: #080808; color: #eee; border: 1px solid #383838; border-radius: 6px; }
spinbutton entry { background: transparent; border: none; color: #fff; padding: 3px; min-width: 36px; }
spinbutton button { border: none; border-radius: 0; padding: 2px; }
entry { color: #eee; background: #101010; border: 1px solid #333; border-radius: 8px; min-height: 20px; padding: 5px; box-shadow: none; }
progressbar trough { background: #292929; min-height: 5px; border: none; border-radius: 4px; }
progressbar progress { background: #eee; min-height: 5px; border: none; border-radius: 4px; }
.history-button { padding: 9px; border-radius: 8px; }
scrollbar { background: #000; } scrollbar slider { background: #444; min-width: 4px; }
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


APP_NAMES = {'reclaim_cgroup': '앱 종료 없는 페이지 회수', 'chrome_web_app_close': 'Chrome 앱 창 종료', 'chrome_tab_highest_priority': 'Chrome 탭 메모리 회수', 'idea': 'IntelliJ IDEA', 'code': 'VS Code', 'datagrip': 'DataGrip',
             'chrome': 'Google Chrome', 'chromium': 'Chromium', 'firefox': 'Firefox',
             'slack': 'Slack', 'discord': 'Discord', 'edge': 'Microsoft Edge',
             'codium': 'VSCodium', 'pycharm': 'PyCharm', 'webstorm': 'WebStorm'}


def app_name(key):
    return APP_NAMES.get(key, key or '없음')


def scenario_text(value):
    try:
        parsed = json.loads(value)
        if parsed[0] == 'reclaim-v1':
            return f'페이지 회수 규칙 · swappiness {parsed[1]}'
        if parsed[0] in ('chrome-tab-v1', 'chrome-web-app-v1'):
            return f'Chrome 탭 회수 규칙 · 최우선 회수 숫자 {parsed[2]} · {parsed[1]}'
        pressure, swap, candidates = parsed
        pressure_text = {'stall': '메모리 압박', 'low-available': '가용 메모리 부족',
                         'psi-unavailable': '압박 지표 확인 불가'}.get(pressure, pressure)
        return f"{pressure_text} · 스왑 I/O {'있음' if swap else '없음'} · 후보 {', '.join(app_name(x) for x in candidates)}"
    except (ValueError, TypeError):
        return '상황 정보 없음'


def event_text(kind, details):
    app = details.get('display_name') or app_name(details.get('app'))
    source = {'skill': '저장된 스킬', 'ai': 'AI 제안', 'local': '로컬 기본 판단', 'emergency': '긴급 로컬 대응'}.get(details.get('source'), '')
    titles = {'ai_request': 'AI 분석 요청', 'ai_proposal': 'AI 제안', 'ai_error': 'AI 분석 실패',
              'skill_saved': '스킬 저장 / 회복 확인', 'recovery_failed': '회복 미확인 / 스킬 해제',
              'optimization_requested': '종료 없는 페이지 회수 요청', 'optimization_error': '페이지 회수 실행 불가', 'optimization_cancelled': '페이지 회수 검증 취소 · 긴급 대응', 'action_cancelled': '보호 또는 대상 변경 · 평가 취소',
              'action_forced': '응답 없는 앱 강제 종료', 'force_blocked': '강제 종료 차단 · 보호 또는 프로세스 변경',
              'action_requested': '앱 종료 요청', 'action_failed': '앱 종료 요청 실패',
              'no_candidates': '현재 앱 종료 후보 없음', 'ai_disabled': 'AI 분석 사용 불가',
              'tab_discarded': 'Chrome 탭 메모리 회수', 'tab_failed': 'Chrome 탭 메모리 회수 실패',
              'ai_test_success': 'AI 연결 테스트 성공', 'ai_test_failed': 'AI 연결 테스트 실패',
              'pressure_warning': '메모리 주의', 'pressure_critical': '메모리 위험'}
    lines = []
    if kind == 'ai_request':
        memory = details.get('memory', {})
        lines.append(f"모델 {details.get('model', '')} · 가용 {memory.get('available_mib', '?')} MiB · 스왑 {memory.get('swap_used_mib', '?')} MiB")
        lines.append('후보: ' + ', '.join(f"{app_name(x.get('id'))} ({x.get('rss_mib', '?')} MiB)" for x in details.get('candidates', [])))
    elif kind == 'ai_proposal':
        lines.append('제안: ' + ('종료 보류' if details.get('app') == 'none' else app))
    elif kind in ('pressure_warning', 'pressure_critical'):
        memory = details.get('memory', {})
        lines.append(f"가용 {memory.get('available_mib', '?')} MiB · 스왑 {memory.get('swap_used_mib', '?')} MiB")
    elif kind in ('ai_test_success', 'ai_test_failed'):
        lines.append('모델: ' + details.get('model', '알 수 없음'))
        if 'error' in details:
            lines.append('오류 유형: ' + details['error'])
    elif kind in ('tab_discarded', 'tab_failed'):
        lines.append('탭 제목과 주소는 기록하지 않습니다.')
    elif kind == 'no_candidates':
        blocked = details.get('blocked', [])
        tabs = details.get('tabs', {})
        lines.extend(app_name(item.get('app')) + ': ' + REASONS.get(item.get('reason'), '대기 중') for item in blocked)
        if tabs.get('profiles'):
            lines.append(f"Chrome 탭: 회수 가능 {tabs.get('eligible', 0)}개 · 일시 제외 {tabs.get('waiting', 0)}개")
        if not blocked and not tabs.get('profiles'):
            lines.append('종료 가능한 앱이 없거나 Chrome 확장이 연결되지 않았습니다.')
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
        super().__init__(title='메모리 관리 · 대응 기록', transient_for=parent)
        self.set_default_size(450, 760)
        self.set_border_width(22)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(root)
        root.pack_start(label('MEMORY PRESSURE MANAGER', 'eyebrow'), False, False, 0)
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
        heading = label(title)
        heading.set_line_wrap(True)
        heading.set_max_width_chars(38)
        box.pack_start(heading, False, False, 0)
        text = label(body, 'muted')
        text.set_max_width_chars(42)
        text.set_line_wrap(True)
        text.set_selectable(True)
        box.pack_start(text, False, False, 0)
        self.content.pack_start(box, False, False, 0)

    def refresh(self):
        for child in self.content.get_children():
            self.content.remove(child)
        try:
            APP_NAMES.update(read_labels(STATE / 'probe.sqlite3'))
            skills, events = read_history(STATE / 'probe.sqlite3')
            self.content.pack_start(label(f'현재 저장된 스킬 · {len(skills)}개', 'eyebrow'), False, False, 0)
            if not skills:
                self.card('저장된 스킬 없음', '대응 후 메모리 회복이 확인되면 저장됩니다.')
            for scenario, app in skills:
                self.card(app_name(app) + (' 규칙' if app in ('chrome_tab_highest_priority', 'chrome_web_app_close') else ' 종료 요청'), scenario_text(scenario))
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
            self.card('기록을 불러올 수 없습니다', 'Memory Pressure Manager가 실행 중인지 확인한 뒤 다시 시도하세요.')
        self.content.show_all()


APP_ICONS = {
    'terminal': ('org.gnome.Terminal', 'org.gnome.Console', 'utilities-terminal'),
    'files': ('org.gnome.Nautilus', 'system-file-manager'),
    'chrome': ('google-chrome', 'chromium', 'web-browser'), 'chromium': ('chromium', 'web-browser'),
    'idea': ('intellij-idea', 'jetbrains-idea', 'com.jetbrains.IntelliJ-IDEA-Community', 'applications-development'),
    'code': ('com.visualstudio.code', 'code', 'visual-studio-code', 'applications-development'),
    'datagrip': ('datagrip', 'jetbrains-datagrip', 'applications-development'),
    'slack': ('slack', 'com.slack.Slack', 'internet-chat'), 'firefox': ('firefox', 'web-browser'),
    'discord': ('discord', 'internet-chat'), 'edge': ('microsoft-edge', 'web-browser'),
    'pycharm': ('pycharm', 'applications-development'), 'webstorm': ('webstorm', 'applications-development'),
}


_desktop_icons = None


def app_icon(key):
    global _desktop_icons
    if _desktop_icons is None:
        _desktop_icons = {}
        names = {'idea': 'intellij', 'code': 'visual studio code', 'datagrip': 'datagrip',
                 'slack': 'slack', 'chrome': 'google chrome', 'firefox': 'firefox',
                 'pycharm': 'pycharm', 'webstorm': 'webstorm', 'discord': 'discord'}
        for app in Gio.AppInfo.get_all():
            title = app.get_name().casefold()
            for ident, match in names.items():
                if match in title and app.get_icon():
                    _desktop_icons[ident] = app.get_icon()
    if key in _desktop_icons:
        image = Gtk.Image.new_from_gicon(_desktop_icons[key], Gtk.IconSize.DIALOG)
        image.set_pixel_size(30)
        image.specific_icon = True
        return image
    bundled = {'idea': 'intellij-idea.svg', 'datagrip': 'datagrip.svg', 'code': 'code.svg', 'slack': 'slack.svg'}.get(key)
    if bundled:
        path = Path(__file__).parent / 'assets' / bundled
        if path.is_file():
            image = Gtk.Image.new_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), 30, 30, True))
            image.specific_icon = True
            return image
    theme = Gtk.IconTheme.get_default()
    for name in APP_ICONS.get(key, (key, 'application-x-executable')):
        if theme.has_icon(name):
            image = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.DIALOG)
            image.set_pixel_size(30)
            image.specific_icon = name not in ('application-x-executable', 'applications-development', 'web-browser')
            return image
    image = Gtk.Image.new_from_icon_name('application-x-executable', Gtk.IconSize.LARGE_TOOLBAR)
    image.specific_icon = False
    return image


def set_favicon(image, data):
    if not data:
        return
    try:
        raw = base64.b64decode(data, validate=True)
        if len(raw) > 16384 or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
            return
        loader = GdkPixbuf.PixbufLoader.new_with_type('png')
        loader.set_size(28, 28)
        loader.write(raw)
        loader.close()
        image.set_from_pixbuf(loader.get_pixbuf())
        return True
    except (ValueError, GLib.Error):
        pass


class Window(Gtk.Window):
    def __init__(self):
        super().__init__(title='메모리 관리')
        self.set_default_size(440, 840)
        self.set_size_request(390, 560)
        self.connect('destroy', self.destroyed)
        bar = Gtk.HeaderBar(show_close_button=True)
        bar.set_title('메모리 관리')
        bar.set_subtitle(BUILD)
        bar.set_decoration_layout('icon:minimize,close')
        self.set_titlebar(bar)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                       margin_start=16, margin_end=16, margin_top=8, margin_bottom=14)
        self.add(root)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        line = Gtk.Box(spacing=10)
        line.pack_start(label('메모리', 'eyebrow'), True, True, 0)
        self.health = label('연결 확인 중', 'badge')
        line.pack_end(self.health, False, False, 0)
        top.pack_start(line, False, False, 0)
        self.metrics = label('—', 'metric')
        top.pack_start(self.metrics, False, False, 0)
        self.ram_bar = Gtk.ProgressBar()
        top.pack_start(self.ram_bar, False, False, 0)
        stats = Gtk.Box(spacing=12, homogeneous=True)
        self.available = self.stat_cell(stats, '사용 가능')
        self.swap = self.stat_cell(stats, '스왑 사용')
        self.cost = self.stat_cell(stats, '이번 달 API')
        self.cost.set_tooltip_text('로컬 추정 비용 · 실제 API 청구액과 다를 수 있습니다')
        top.pack_start(stats, False, False, 3)
        root.pack_start(top, False, False, 0)
        state = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        state.get_style_context().add_class('frame')
        self.note = label('메모리 상태 확인 중', 'muted')
        self.note.set_line_wrap(True)
        self.ai_note = label('AI 연결 상태 확인 중', 'muted')
        self.ai_note.set_line_wrap(True)
        state.pack_start(self.note, False, False, 0)
        state.pack_start(self.ai_note, False, False, 0)
        root.pack_start(state, False, False, 0)
        filters = Gtk.Box(spacing=4)
        filters.get_style_context().add_class('filter')
        group = None
        for key, text in (('all', '전체'), ('apps', '프로세스'), ('tabs', 'Chrome 탭')):
            button = Gtk.RadioButton.new_with_label_from_widget(group, text)
            button.set_mode(False)
            if group is None:
                group = button
            button.connect('toggled', self.filter_changed, key)
            filters.pack_start(button, True, True, 0)
        self.filter = 'all'
        root.pack_start(filters, False, False, 0)
        self.search = Gtk.SearchEntry(placeholder_text='앱·프로세스 또는 탭 제목 검색')
        self.search.connect('search-changed', lambda *_: self.apply_filter())
        root.pack_start(self.search, False, False, 0)
        self.rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.tab_rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        compare = lambda a, b: (a.sort_key > b.sort_key) - (a.sort_key < b.sort_key)
        self.rows.set_sort_func(compare)
        self.tab_rows.set_sort_func(compare)
        self.apps_heading = label('앱 · 프로세스', 'eyebrow')
        self.tabs_heading = label('CHROME 탭', 'eyebrow')
        self.tab_status = label('Chrome 연결 확인 중', 'muted')
        self.tab_status.set_line_wrap(True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        for child in (self.apps_heading, self.rows, self.tabs_heading, self.tab_status, self.tab_rows):
            content.pack_start(child, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(content)
        root.pack_start(scroll, True, True, 0)
        help_text = label('숫자가 작을수록 중요 · 보호를 켜면 자동 정리 제외', 'muted')
        help_text.set_line_wrap(True)
        root.pack_start(help_text, False, False, 0)
        self.history_button = Gtk.Button(label='대응 기록 · 저장된 스킬')
        self.history_button.get_style_context().add_class('history-button')
        self.history_button.connect('clicked', self.show_history)
        root.pack_start(self.history_button, False, False, 0)
        self.history_window = None
        self.entries, self.tabs, self.tab_pending = {}, {}, {}
        self.render()
        self.timer = GLib.timeout_add_seconds(3, self.render)

    def destroyed(self, *_):
        if getattr(self, 'timer', None):
            GLib.source_remove(self.timer)
        Gtk.main_quit()

    @staticmethod
    def stat_cell(parent, caption):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.pack_start(label(caption, 'muted'), False, False, 0)
        value = label('—', 'stat')
        box.pack_start(value, False, False, 0)
        parent.pack_start(box, True, True, 0)
        return value

    def filter_changed(self, button, key):
        if button.get_active():
            self.filter = key
            if hasattr(self, 'entries'):
                self.apply_filter()

    def apply_filter(self):
        query = self.search.get_text().casefold().strip()
        for entries, section in ((self.entries, 'apps'), (self.tabs, 'tabs')):
            for entry in entries.values():
                entry['row'].set_visible(self.filter in ('all', section) and query in entry['name'].casefold())
        self.apps_heading.set_visible(self.filter != 'tabs')
        self.tabs_heading.set_visible(self.filter != 'apps')
        self.tab_status.set_visible(self.filter != 'apps')

    def show_history(self, *_):
        if self.history_window is None:
            self.history_window = HistoryWindow(self)
            self.history_window.connect('destroy', lambda *_: setattr(self, 'history_window', None))
            self.history_window.show_all()
        else:
            self.history_window.refresh()
            self.history_window.present()

    def create_row(self, name, icon, value, callback, key):
        row = Gtk.ListBoxRow()
        row.sort_key = (True, 0, '')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        row.add(box)
        top = Gtk.Box(spacing=10)
        top.pack_start(icon, False, False, 0)
        title = label(name, 'app-name')
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(25)
        title.set_hexpand(True)
        top.pack_start(title, True, True, 0)
        memory = label('', 'memory')
        top.pack_end(memory, False, False, 0)
        box.pack_start(top, False, False, 0)
        bottom = Gtk.Box(spacing=8)
        protect = Gtk.ToggleButton(label='보호')
        protect.get_style_context().add_class('protect')
        protect.set_tooltip_text('자동 종료와 탭 회수에서 제외')
        protect.set_active(value == 0)
        number = Gtk.SpinButton.new_with_range(1, 999, 1)
        number.set_numeric(True)
        number.set_width_chars(3)
        number.set_value(value or 500)
        number.set_sensitive(value != 0)
        number.set_tooltip_text('1~999 · 숫자가 클수록 먼저 정리')
        bottom.pack_start(protect, False, False, 0)
        bottom.pack_start(number, False, False, 0)
        detail = label('', 'muted')
        detail.set_ellipsize(Pango.EllipsizeMode.END)
        detail.set_max_width_chars(22)
        bottom.pack_end(detail, True, True, 0)
        box.pack_start(bottom, False, False, 0)
        entry = dict(row=row, title=title, memory=memory, protect=protect, number=number,
                     detail=detail, icon=icon, name=name)
        entry['handlers'] = (protect.connect('toggled', callback, key), number.connect('value-changed', callback, key))
        return entry

    @staticmethod
    def update_controls(entry, value, supported=True):
        protect, number = entry['protect'], entry['number']
        if protect.has_focus() or number.has_focus():
            return
        protect.handler_block(entry['handlers'][0])
        number.handler_block(entry['handlers'][1])
        protect.set_active(value == 0)
        if value:
            number.set_value(value)
        protect.set_sensitive(supported)
        number.set_sensitive(supported and value != 0)
        protect.handler_unblock(entry['handlers'][0])
        number.handler_unblock(entry['handlers'][1])

    def changed(self, widget, key):
        entry = self.entries[key]
        entry['number'].set_sensitive(not entry['protect'].get_active())
        try:
            update_priority(key, 0 if entry['protect'].get_active() else entry['number'].get_value_as_int())
        except OSError:
            self.note.set_text('설정 저장 실패 · 실행 상태를 확인하세요')

    def tab_changed(self, widget, key):
        entry = self.tabs[key]
        entry['number'].set_sensitive(not entry['protect'].get_active())
        value = 0 if entry['protect'].get_active() else entry['number'].get_value_as_int()
        try:
            enqueue(RUNTIME, key[0], key[1], key[2], value)
            self.tab_pending[key] = (value, time.monotonic() + 12)
            entry['detail'].set_text('저장 중…')
        except (OSError, ValueError):
            entry['detail'].set_text('Chrome 연결 확인 필요')

    def render_tabs(self):
        try:
            profiles = snapshots(RUNTIME)
        except (OSError, ValueError):
            profiles = []
        items = [(p['profile'], t) for p in profiles for t in p['tabs']]
        self.tabs_heading.set_text(f'CHROME 탭  {len(items)}')
        outdated = any(p.get('build') != BUILD for p in profiles)
        self.tab_status.set_text('Chrome 확장 업데이트 필요 · 확장 페이지에서 새로고침하세요' if outdated else f'{len(profiles)}개 프로필 연결 · 탭별 메모리는 Chrome이 제공하지 않습니다'
                                 if profiles else 'Chrome 확장을 연결하면 열린 탭이 여기에 표시됩니다')
        keys = {(p, t['id'], t['token']) for p, t in items}
        for key in self.tabs.keys() - keys:
            self.tab_rows.remove(self.tabs.pop(key)['row'])
            self.tab_pending.pop(key, None)
        for position, (profile, item) in enumerate(items):
            key = (profile, item['id'], item['token'])
            if key not in self.tabs:
                self.tabs[key] = self.create_row(item['title'], app_icon('chrome'), item['priority'], self.tab_changed, key)
                self.tabs[key]['favicon'] = None
                self.tab_rows.add(self.tabs[key]['row'])
                self.tabs[key]['row'].show_all()
            entry = self.tabs[key]
            entry['name'] = item['title']
            entry['title'].set_text(item['title'])
            entry['title'].set_tooltip_text(item['title'])
            if item.get('favicon') and item['favicon'] != entry['favicon']:
                if set_favicon(entry['icon'], item['favicon']):
                    entry['favicon'] = item['favicon']
            entry['row'].sort_key = (not bool(entry['favicon']), position)
            pending = self.tab_pending.get(key)
            if pending and (pending[0] == item['priority'] or pending[1] < time.monotonic()):
                self.tab_pending.pop(key, None)
                pending = None
            flags = [name for flag, name in (('active', '사용 중'), ('pinned', '고정'),
                     ('audible', '소리 재생'), ('discarded', '회수됨')) if item[flag]]
            if item.get('standalone'):
                flags = ['Chrome 앱 · 창 종료', *[f for f in flags if f != '사용 중']]
                flags.append('사용 중' if item.get('windowFocused') else '백그라운드')
            entry['memory'].set_text('회수됨' if item['discarded'] else '')
            entry['detail'].set_text('저장 중…' if pending else ' · '.join(flags or [('Chrome 앱 · 창 종료' if item.get('standalone') else '프로필 ' + profile[:4])]))
            if not pending:
                self.update_controls(entry, item['priority'], item['supported'])

        self.tab_rows.invalidate_sort()

    def render(self):
        data = read(RUNTIME / 'status.json')
        active = time.time() - data.get('timestamp', 0) <= 75
        if not active:
            self.metrics.set_text('— %')
            self.health.set_text('감시 중지')
            self.note.set_text('Memory Pressure Manager 서비스가 실행 중이 아닙니다')
            self.ai_note.set_text('서비스를 시작하면 자동 대응을 준비합니다')
            self.available.set_text('—'); self.swap.set_text('—'); self.cost.set_text('—')
            self.ram_bar.set_fraction(0)
            data = {}
        else:
            usage = data['usage']
            self.metrics.set_text(f'{usage}%')
            self.health.set_text({'normal': '감시 중', 'warning': '메모리 주의', 'critical': '대응 필요'}.get(data['level'], '감시 중'))
            self.ram_bar.set_fraction(min(1, max(0, usage / 100)))
            self.available.set_text(f"{data['available_mib'] / 1024:.1f} GiB")
            self.swap.set_text(f"{data.get('swap_used_mib', 0) / 1024:.1f} GiB")
            self.cost.set_text(f"${data['cost_micro'] / 1e6:.4f}")
            self.note.set_text(data['status'] if data.get('build') == BUILD else '구버전 서비스 실행 중 · 재설치 필요')
            self.note.set_tooltip_text('실행 위치: ' + data.get('source_dir', '구버전: 위치 정보 없음') + '\n빌드: ' + data.get('build', '구버전'))
            self.ai_note.set_text('AI · ' + data.get('ai_status', '연결 상태 확인 필요') + '\n' + data.get('optimization_status', ''))
            self.history_button.set_label(f"대응 기록 보기    ·    저장된 스킬 {data['learned_skills']}")
        saved = read_priorities(STATE / 'priorities.json')
        items = data.get('apps', [])
        self.apps_heading.set_text(f'앱 · 프로세스  {len(items)}')
        current = {t['id'] for t in items}
        for key in self.entries.keys() - current:
            self.rows.remove(self.entries.pop(key)['row'])
        for item in items:
            key = item['id']
            if key not in self.entries:
                self.entries[key] = self.create_row(item['name'], app_icon(key), item['priority'], self.changed, key)
                self.rows.add(self.entries[key]['row'])
                self.entries[key]['row'].show_all()
            entry = self.entries[key]
            entry['row'].sort_key = (not getattr(entry['icon'], 'specific_icon', False), -item['rss_mib'], item['name'])
            size = item['rss_mib']
            entry['memory'].set_text(f'{size / 1024:.1f} GiB' if size >= 1024 else f'{size:,} MiB')
            reason = REASONS.get(item.get('blocked_reason'), '자동 대응 가능')
            entry['detail'].set_text(reason)
            entry['detail'].set_tooltip_text(reason)
            self.update_controls(entry, saved.get(key, item['priority']), item.get('blocked_reason') != 'system_process')
        self.rows.invalidate_sort()
        self.render_tabs()
        self.apply_filter()
        return True


if __name__ == '__main__':
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    window = Window()
    window.show_all()
    Gtk.main()
