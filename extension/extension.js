import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Clutter from 'gi://Clutter';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

export default class ProbeExtension extends Extension {
    enable() {
        this.button = new PanelMenu.Button(0, 'Probe');
        this.label = new St.Label({text: 'RAM —   $—', y_align: Clutter.ActorAlign.CENTER});
        this.button.add_child(this.label);
        const manage = new PopupMenu.PopupMenuItem('메모리 우선순위 열기');
        manage.connect('activate', () => {
            try {
                Gio.Subprocess.new(['/usr/bin/python3', this.path + '/window.py'], Gio.SubprocessFlags.NONE);
            } catch (e) {
                Main.notify('Probe', 'GTK 창을 열 수 없습니다.');
            }
        });
        this.button.menu.addMenuItem(manage);
        Main.panel.addToStatusArea(this.uuid, this.button);
        this.refresh();
        this.timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 3, () => {
            this.refresh();
            return GLib.SOURCE_CONTINUE;
        });
    }
    refresh() {
        const base = GLib.getenv('XDG_RUNTIME_DIR') || GLib.get_user_cache_dir();
        try {
            const [ok, bytes] = GLib.file_get_contents(GLib.build_filenamev([base, 'probe', 'status.json']));
            if (!ok) throw new Error('Cannot read status');
            const status = JSON.parse(new TextDecoder().decode(bytes));
            if (Date.now() / 1000 - status.timestamp > 75) throw new Error('Stale status');
            this.label.text = `RAM ${status.usage}%   $${(status.cost_micro / 1e6).toFixed(4)}`;
        } catch (e) {
            this.label.text = 'RAM —   $—';
        }
    }
    disable() {
        if (this.timer) GLib.source_remove(this.timer);
        this.timer = null;
        this.button?.destroy();
        this.button = null;
    }
}
