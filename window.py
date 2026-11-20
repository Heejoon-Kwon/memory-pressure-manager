#!/usr/bin/python3
"""GTK view and local priorities editor. Uses Ubuntu's GTK instead of the venv."""
import json
import os
import tempfile
import time
from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk

STATE = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'probe'
RUNTIME = Path(os.environ.get('XDG_RUNTIME_DIR') or os.environ.get('XDG_CACHE_HOME')
               or Path.home() / '.cache') / 'probe'
OPTIONS = ('보호', '먼저 닫기', '다음에 닫기', '마지막에 닫기', '유지')
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
    values = read(path)
    values[key] = value
    fd, tmp = tempfile.mkstemp(dir=STATE)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(values, f)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


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
        for text in ('보호  ·  자동 종료 대상에서 제외하고 시스템 보호를 시도합니다.',
                     '유지  ·  Probe가 자동으로 종료하지 않습니다.',
                     '먼저 / 다음 / 마지막에 닫기  ·  메모리가 위험할 때 이 순서대로 종료를 요청합니다.'):
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
        self.entries = {}
        self.render()
        GLib.timeout_add_seconds(3, self.render)

    def changed(self, combo, key):
        try:
            update_priority(key, combo.get_active())
        except OSError:
            self.note.set_text('우선순위를 저장할 수 없습니다. Probe가 실행 중인지 확인하세요.')

    def add_app(self, item):
        key = item['id']
        row = Gtk.ListBoxRow()
        line = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8)
        row.add(line)
        name = label(item['name'])
        line.pack_start(name, True, True, 0)
        memory = label(style='muted')
        line.pack_start(memory, False, False, 0)
        combo = Gtk.ComboBoxText()
        for option in OPTIONS:
            combo.append_text(option)
        combo.set_active(item['priority'])
        handler = combo.connect('changed', self.changed, key)
        line.pack_end(combo, False, False, 0)
        self.rows.add(row)
        row.show_all()
        self.entries[key] = (row, memory, combo, handler)

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
        saved = read(STATE / 'priorities.json')
        items = sorted(data.get('apps', []), key=lambda item: (-item['rss_mib'], item['name']))[:12]
        current = {item['id'] for item in items}
        for key in self.entries.keys() - current:
            self.rows.remove(self.entries.pop(key)[0])
        for item in items:
            key = item['id']
            if key not in self.entries:
                self.add_app(item)
            row, memory, combo, handler = self.entries[key]
            memory.set_text(f"약 {item['rss_mib']:,} MiB")
            if not combo.has_focus():
                # Programmatic updates must not save defaults or overwrite a user change.
                combo.handler_block(handler)
                combo.set_active(saved.get(key, item['priority']))
                combo.handler_unblock(handler)
            combo.set_tooltip_text('RSS 합계. 공유 메모리가 겹칠 수 있습니다.'
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
