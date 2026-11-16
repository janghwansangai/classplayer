"""업데이트 바꿔 끼우기 (윈도우). Qt 없이 돈다.

받은 새 버전의 ClassPlayer.exe 를 `--apply-update <옛 PID> <설치 폴더>` 로 실행하면:
1) 옛 앱이 끝나기를 기다리고(기본 30초), 그래도 남아 있으면 강제로 끝낸다
2) 새 파일을 설치 폴더 위에 덮어쓴다. 쓰는 중이라 잠긴 파일은 이름을 바꿔 비켜 두고 새 파일을 넣는다
   (윈도우는 실행 중인 exe·dll 도 이름 바꾸기는 된다). 비켜 둔 *.cpold 는 다음 실행 때 지운다
3) 제어판 '앱' 목록의 버전 표시를 새 버전으로 고치고, 새 앱을 실행한다
모든 단계는 %TEMP%\\ClassPlayerUpdate\\update.log 에 남는다. 실패하면 알림 창을 띄우고 릴리스 페이지를 연다.

예전 방식(.bat 스크립트)은 2026-11-17 윈도우 실사용에서 '종료 뒤 다시 안 열리고 옛 버전 그대로'로 실패했다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

OLD_SUFFIX = ".cpold"
INNO_APP_ID = "{6C1F3B0E-7A2D-4F0B-9C4E-2B7C1A5D9E31}_is1"
RELEASES_PAGE = "https://github.com/janghwansangai/classplayer/releases/latest"
IS_WIN = sys.platform.startswith("win")

_log_file: Path | None = None


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        if _log_file:
            with open(_log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------- 프로세스
def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WIN:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x00100000 | 0x1000, False, pid)      # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            return k.WaitForSingleObject(h, 0) == 0x102            # WAIT_TIMEOUT = 아직 실행 중
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_pid(pid: int) -> None:
    if IS_WIN:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x0001 | 0x00100000, False, pid)        # TERMINATE | SYNCHRONIZE
        if h:
            k.TerminateProcess(h, 1)
            k.WaitForSingleObject(h, 5000)
            k.CloseHandle(h)
    else:
        import signal
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def clean_env() -> dict[str, str]:
    """PyInstaller 로 묶인 앱이 다른 묶인 앱(또는 자기 자신)을 '독립된 새 앱'으로 띄우기 위한 환경."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI_") and k not in ("_MEIPASS2",)}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def launch_detached(args: list[str], cwd: str | None = None) -> subprocess.Popen:
    """부모와 상관없이 계속 도는 새 프로세스. 부모가 작업 개체 안에 있어도 빠져나가도록 시도한다."""
    if IS_WIN:
        base = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        for flags in (base | 0x01000000, base):  # + CREATE_BREAKAWAY_FROM_JOB (허용 안 되면 빼고)
            try:
                return subprocess.Popen(args, cwd=cwd, env=clean_env(), creationflags=flags, close_fds=True,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                continue
        raise OSError("프로세스를 시작하지 못했습니다")
    return subprocess.Popen(args, cwd=cwd, env=clean_env(), start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------------- 파일 바꾸기
def copy_file(src: Path, dst: Path, tries: int = 10) -> None:
    last: Exception | None = None
    for attempt in range(tries):
        try:
            shutil.copy2(src, dst)
            return
        except OSError as e:
            last = e
            if attempt >= 1 and dst.exists():
                # 잠긴 파일(남아 있는 프로그램이 쓰는 중): 이름을 바꿔 비켜 두고 새 파일을 넣는다
                try:
                    aside = dst.with_name(f"{dst.name}.{int(time.time() * 1000)}{OLD_SUFFIX}")
                    os.replace(dst, aside)
                    log(f"  잠긴 파일 비켜 둠: {dst.name}")
                    continue
                except OSError:
                    pass
            time.sleep(0.5)
    raise OSError(f"{dst} 를 바꾸지 못했습니다: {last}")


def copy_tree(src: Path, dst: Path) -> int:
    n = 0
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for f in files:
            copy_file(Path(root) / f, dst / rel / f)
            n += 1
    return n


def cleanup_old_files(folder: Path) -> int:
    """지난 업데이트 때 비켜 둔 *.cpold 지우기 (앱 시작 때 뒤에서)."""
    n = 0
    for p in folder.rglob(f"*{OLD_SUFFIX}"):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def set_installed_version(version: str) -> None:
    """설치 프로그램(Inno Setup)이 남긴 '앱 목록' 버전 표시를 고친다."""
    if not IS_WIN:
        return
    try:
        import winreg
        path = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{INNO_APP_ID}"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, version)
        log(f"앱 목록 버전 표시: {version}")
    except OSError:
        pass


def alert(text: str) -> None:
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "ClassPlayer 업데이트", 0x30 | 0x40000)  # 경고 아이콘, 맨 앞
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------- 실행
def apply(old_pid: int, install_dir: Path, src_dir: Path, wait_s: float = 30.0, launch: bool = True) -> bool:
    log(f"시작: PID {old_pid}, 설치 폴더 {install_dir}, 새 파일 {src_dir}")
    if src_dir.resolve() == install_dir.resolve():
        log("새 파일과 설치 폴더가 같아 그만둠")
        return False
    t0 = time.time()
    while pid_alive(old_pid) and time.time() - t0 < wait_s:
        time.sleep(0.25)
    if pid_alive(old_pid):
        log(f"옛 앱이 {wait_s:.0f}초 안에 끝나지 않아 강제로 끝냄")
        kill_pid(old_pid)
        time.sleep(1.0)
    else:
        log(f"옛 앱 종료 확인 ({time.time() - t0:.1f}초)")
        time.sleep(0.5)          # 파일 잠금이 풀릴 시간
    try:
        n = copy_tree(src_dir, install_dir)
        log(f"파일 {n}개 바꿈")
    except OSError as e:
        log(f"실패: {e}")
        alert("업데이트를 마치지 못했습니다.\n\n" + str(e) +
              "\n\n열리는 홈페이지에서 설치 파일(ClassPlayer-Setup)을 받아 실행해 주세요.")
        try:
            os.startfile(RELEASES_PAGE) if IS_WIN else None  # type: ignore[attr-defined]
        except OSError:
            pass
        exe = install_dir / ("ClassPlayer.exe" if IS_WIN else "ClassPlayer")
        if launch and exe.exists():
            launch_detached([str(exe)], cwd=str(install_dir))
        return False
    try:
        from classplayer import __version__
        set_installed_version(__version__)
    except Exception:  # noqa: BLE001
        pass
    exe = install_dir / ("ClassPlayer.exe" if IS_WIN else "ClassPlayer")
    if launch:
        p = launch_detached([str(exe)], cwd=str(install_dir))
        log(f"새 앱 실행: PID {p.pid}")
    else:
        log("실행 생략 (시험)")
    return True


def main(argv: list[str]) -> int:
    """ClassPlayer.exe --apply-update <PID> <설치 폴더>"""
    global _log_file
    work = Path(tempfile.gettempdir()) / "ClassPlayerUpdate"
    work.mkdir(parents=True, exist_ok=True)
    _log_file = Path(os.environ.get("CLASSPLAYER_UPDATE_LOG") or work / "update.log")
    try:
        old_pid, install_dir = int(argv[0]), Path(argv[1])
    except (IndexError, ValueError):
        log(f"잘못된 인자: {argv}")
        return 2
    src_dir = Path(sys.executable).resolve().parent        # 지금 실행 중인 새 버전 폴더
    wait_s = float(os.environ.get("CLASSPLAYER_UPDATE_WAIT", "30"))
    launch = not os.environ.get("CLASSPLAYER_UPDATE_NO_LAUNCH")
    try:
        ok = apply(old_pid, install_dir, src_dir, wait_s, launch)
    except Exception as e:  # noqa: BLE001
        log(f"예상 못 한 오류: {type(e).__name__}: {e}")
        alert(f"업데이트 중 오류가 났습니다: {e}")
        return 1
    return 0 if ok else 1
