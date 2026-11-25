import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Clutter from 'gi://Clutter';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

export default class MemoraExtension extends Extension {
    enable() {
        this.button = new PanelMenu.Button(0, 'Memora');
        this.label = new St.Label({text: 'RAM —   $—', y_align: Clutter.ActorAlign.CENTER});
        this.button.add_child(this.label);
        const manage = new PopupMenu.PopupMenuItem('메모리 관리 열기');
        manage.connect('activate', () => {
            try {
                this.window = Gio.Subprocess.new(['/usr/bin/python3', this.path + '/window.py'], Gio.SubprocessFlags.NONE);
            } catch (e) {
                Main.notify('Memora', 'GTK 창을 열 수 없습니다.');
            }
        });
        this.button.menu.addMenuItem(manage);
        this.button.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const quit = new PopupMenu.PopupMenuItem('Memora 종료');
        quit.connect('activate', () => this.stopService());
        this.button.menu.addMenuItem(quit);
        Main.panel.addToStatusArea(this.uuid, this.button);
        this.button.container.hide();
        this.refresh();
        this.timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 3, () => {
            this.refresh();
            return GLib.SOURCE_CONTINUE;
        });
    }
    stopService() {
        try {
            const proc = Gio.Subprocess.new(['/usr/bin/systemctl', '--user', 'stop', 'memora.service'], Gio.SubprocessFlags.STDERR_PIPE);
            proc.communicate_utf8_async(null, null, (source, result) => {
                try {
                    const [, , stderr] = source.communicate_utf8_finish(result);
                    if (!source.get_successful()) throw new Error(stderr || '종료 실패');
                    if (this.window && !this.window.get_if_exited()) this.window.force_exit();
                    this.button?.container.hide();
                } catch (e) { Main.notify('Memora', '서비스를 종료하지 못했습니다.'); }
            });
        } catch (e) { Main.notify('Memora', '서비스 종료 명령을 실행할 수 없습니다.'); }
    }
    refresh() {
        const base = GLib.getenv('XDG_RUNTIME_DIR') || GLib.get_user_cache_dir();
        try {
            const [ok, bytes] = GLib.file_get_contents(GLib.build_filenamev([base, 'probe', 'status.json']));
            if (!ok) throw new Error('Cannot read status');
            const status = JSON.parse(new TextDecoder().decode(bytes));
            if (Date.now() / 1000 - status.timestamp > 75) throw new Error('Stale status');
            this.label.text = `RAM ${status.usage}%   $${(status.cost_micro / 1e6).toFixed(4)}`;
            this.button.container.show();
        } catch (e) {
            this.button.container.hide();
        }
    }
    disable() {
        if (this.timer) GLib.source_remove(this.timer);
        this.timer = null;
        this.button?.destroy();
        this.button = null;
    }
}
