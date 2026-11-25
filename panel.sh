#!/bin/sh
set -eu
uuid=probe@local
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if ! command -v gnome-extensions >/dev/null; then
    printf '%s\n' 'GNOME 확장 명령을 찾지 못했습니다. Ubuntu GNOME 세션을 확인하세요.'
elif ! installed=$(gnome-extensions list); then
    printf '%s\n' 'GNOME Shell에 연결하지 못했습니다. Ubuntu 화면에서 연 터미널에서 실행하세요.'
elif ! printf '%s\n' "$installed" | grep -Fxq "$uuid"; then
    if [ ! -f "${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$uuid/metadata.json" ]; then
        printf '%s\n' 'Memora 확장 파일이 없습니다. ./install.sh를 실행하세요.'
    elif /usr/bin/python3 "$root/enable_panel_at_login.py"; then
        printf '%s\n' 'GNOME이 새 확장을 아직 읽지 않았습니다. 다음 로그인에 활성화되도록 등록했습니다.'
        printf '%s\n' '상단 바에 표시하려면 작업을 저장하고 로그아웃한 뒤 다시 로그인하세요.'
    fi
else
    gnome-extensions enable "$uuid" || true
    if gnome-extensions list --active | grep -Fxq "$uuid"; then
        printf '%s\n' 'GNOME 상단 바의 RAM과 비용 표시가 활성화됐습니다.'
        exit 0
    fi
    if [ "$(gsettings get org.gnome.shell disable-user-extensions 2>/dev/null || true)" = true ]; then
        printf '%s\n' 'GNOME의 사용자 확장이 전체 꺼져 있습니다. 확장 앱에서 전체 확장을 켜세요.'
    else
        printf '%s\n' 'GNOME이 Memora 확장을 활성 상태로 보고하지 않습니다.'
    fi
    gnome-extensions info "$uuid" || true
fi

printf '%s\n' '상단 바 없이 관리 창을 열려면 Memora 폴더에서 /usr/bin/python3 window.py 를 실행하세요.'
