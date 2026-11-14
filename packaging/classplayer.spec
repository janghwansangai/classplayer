# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec. 실행: uv run pyinstaller packaging/classplayer.spec (프로젝트 루트에서)"""
import os
import shutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"


def find(name, extra_dirs=()):
    exe = name + (".exe" if IS_WIN else "")
    for d in list(extra_dirs) + [ROOT / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")]:
        p = Path(d) / exe
        if p.exists():
            return str(p)
    found = shutil.which(name)
    return found


binaries = []
# 재생 엔진 libmpv
if IS_MAC:
    for cand in ("/opt/homebrew/lib/libmpv.2.dylib", "/usr/local/lib/libmpv.2.dylib"):
        if os.path.exists(cand):
            binaries.append((cand, "."))
            break
elif IS_WIN:
    for cand in (ROOT / "bin" / "libmpv-2.dll", ROOT / "bin" / "mpv-2.dll"):
        if cand.exists():
            binaries.append((str(cand), "."))
            break

# 외부 도구: ffmpeg/ffprobe(합치기, 메타데이터), deno 또는 node(유튜브 JS 계산)
for tool in ("ffmpeg", "ffprobe"):
    p = find(tool)
    if p:
        binaries.append((p, "."))
js = find("deno") or find("node")
if js:
    binaries.append((js, "."))
# PO 토큰 실행 파일 (packaging/fetch_bgutil.sh 또는 CI 가 bin/ 에 받아 둠). 없으면 건너뜀.
pot = ROOT / "bin" / ("bgutil-pot.exe" if IS_WIN else "bgutil-pot")
if pot.exists():
    binaries.append((str(pot), "."))

datas = [(str(SRC / "classplayer" / "assets" / "icon.png"), "assets"),
         # yt-dlp 가 파일 시스템에서 찾는 플러그인이라 .py 그대로 넣는다 (bootstrap.enable_po_token_plugin)
         (str(SRC / "classplayer" / "vendor" / "yt_dlp_plugins"), "classplayer_vendor/yt_dlp_plugins")] + collect_data_files("yt_dlp_ejs") + collect_data_files("yt_dlp", includes=["**/*.json"])
hiddenimports = (collect_submodules("yt_dlp") + collect_submodules("yt_dlp_ejs") + collect_submodules("OpenGL")
                 + collect_submodules("curl_cffi") + ["mpv"])

a = Analysis(
    [str(SRC / "classplayer" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtPdf",
        "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSensors", "PySide6.QtSerialPort",
        "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
        "PySide6.QtLocation", "PySide6.QtPositioning", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
        "PySide6.QtHttpServer", "PySide6.QtTextToSpeech", "PySide6.QtGraphs", "PySide6.QtSpatialAudio",
        "tkinter", "numpy", "pytest",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ClassPlayer",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    target_arch=None,
    codesign_identity=os.environ.get("CODESIGN_IDENTITY"),
    entitlements_file=None,
    icon=str(ROOT / "packaging" / ("icon.ico" if IS_WIN else "icon.icns")),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ClassPlayer")

if IS_MAC:
    app = BUNDLE(
        coll,
        name="ClassPlayer.app",
        icon=str(ROOT / "packaging" / "icon.icns") if (ROOT / "packaging" / "icon.icns").exists() else None,
        bundle_identifier="kr.classplayer.app",
        info_plist={
            "CFBundleDisplayName": "ClassPlayer",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "NSRequiresAquaSystemAppearance": False,
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Video",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": ["public.movie", "public.mpeg-4", "public.avi", "org.matroska.mkv"],
            }],
        },
    )
