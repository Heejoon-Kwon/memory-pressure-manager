"""Register the installed extension for Shell's next startup."""
import sys

from gi.repository import Gio


def main():
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup('org.gnome.shell', True) if source else None
    if schema is None:
        print('GNOME Shell 설정을 찾지 못했습니다.')
        return 1
    settings = Gio.Settings.new_full(schema, None, None)
    if schema.has_key('allow-extension-installation') and not settings.get_boolean('allow-extension-installation'):
        print('현재 GNOME 설정은 사용자 확장 설치를 허용하지 않습니다.')
        return 1
    if settings.get_boolean('disable-user-extensions'):
        print('GNOME의 사용자 확장이 전체 꺼져 있습니다. 확장 앱에서 전체 확장을 켜세요.')
        return 1
    enabled = settings.get_strv('enabled-extensions')
    if 'probe@local' not in enabled:
        enabled.append('probe@local')
        if not settings.set_strv('enabled-extensions', enabled):
            print('GNOME 확장 활성화 설정을 저장할 수 없습니다.')
            return 1
        Gio.Settings.sync()
    return 0


if __name__ == '__main__':
    sys.exit(main())
