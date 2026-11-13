"""윈도우 전용: 기본 영상 플레이어로 등록 (현재 사용자 범위 레지스트리)."""
from __future__ import annotations

import os
import sys

from classplayer.bootstrap import VIDEO_EXTENSIONS

PROG_ID = "ClassPlayer.Video"
APP_KEY = r"Software\ClassPlayer"


def executable_path() -> str:
    if getattr(sys, "frozen", False):
        return sys.executable
    return f'{sys.executable} -m classplayer'


def register_default_player() -> str:
    """파일 연결을 등록하고 윈도우 '기본 앱' 설정 화면을 연다. 돌려주는 값은 안내 문구."""
    if sys.platform != "win32":
        return "윈도우에서만 지원합니다."
    import winreg  # noqa: WPS433

    exe = executable_path()
    cmd = f'"{exe}" "%1"' if getattr(sys, "frozen", False) else f'{exe} "%1"'
    hkcu = winreg.HKEY_CURRENT_USER

    def setval(path: str, name: str | None, value: str) -> None:
        with winreg.CreateKey(hkcu, path) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)

    setval(rf"Software\Classes\{PROG_ID}", None, "ClassPlayer 영상")
    setval(rf"Software\Classes\{PROG_ID}\DefaultIcon", None, f'"{sys.executable}",0')
    setval(rf"Software\Classes\{PROG_ID}\shell\open\command", None, cmd)
    setval(rf"Software\Classes\Applications\ClassPlayer.exe\shell\open\command", None, cmd)
    setval(rf"{APP_KEY}\Capabilities", "ApplicationName", "ClassPlayer")
    setval(rf"{APP_KEY}\Capabilities", "ApplicationDescription", "수업용 영상 플레이어")
    for ext in sorted(VIDEO_EXTENSIONS):
        setval(rf"Software\Classes\{ext}\OpenWithProgIds", PROG_ID, "")
        setval(rf"{APP_KEY}\Capabilities\FileAssociations", ext, PROG_ID)
    setval(r"Software\RegisteredApplications", "ClassPlayer", rf"{APP_KEY}\Capabilities")
    try:
        os.startfile("ms-settings:defaultapps")  # type: ignore[attr-defined]
    except OSError:
        pass
    return ("등록했습니다. 열린 '기본 앱' 설정에서 ClassPlayer를 찾아 '기본값으로 설정'을 누르거나, "
            "영상 파일을 우클릭 > 연결 프로그램 > ClassPlayer 를 고르고 '항상 사용'에 체크하세요.")
