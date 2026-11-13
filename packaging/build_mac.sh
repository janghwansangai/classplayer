#!/bin/bash
# 맥용 ClassPlayer.app 빌드. 사전 준비: brew install mpv ffmpeg deno uv && uv sync
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf build dist
uv run pyinstaller --noconfirm --clean packaging/classplayer.spec
APP=dist/ClassPlayer.app
# python-mpv 는 'libmpv.dylib' 이름으로 찾으므로 버전 붙은 파일에 링크를 건다
( cd "$APP/Contents/Frameworks" && [ -e libmpv.dylib ] || ln -s libmpv.2.dylib libmpv.dylib )
# PyInstaller 가 임시 서명을 하지만, 전체를 한 번 더 ad-hoc 서명해 Gatekeeper 가 "손상됨"으로 보지 않게 한다
codesign --force --deep --sign "${CODESIGN_IDENTITY:--}" "$APP" 2>/dev/null || true
if [ "${SKIP_SELFTEST:-0}" = "1" ]; then
  ditto -c -k --keepParent "$APP" "dist/ClassPlayer-mac-$(uname -m).zip"; du -sh "$APP" dist/*.zip; echo "완료(자가 점검 생략): $APP"; exit 0
fi
echo "== 자가 점검 =="
OUT=$(CLASSPLAYER_SELFTEST="$PWD/tests/assets/test_hevc_4k.mp4" "$APP/Contents/MacOS/ClassPlayer" 2>/dev/null | grep SELFTEST) || { echo "자가 점검 실패"; exit 1; }
echo "$OUT"
echo "$OUT" | grep -q '"libmpv": "[^"]*ClassPlayer.app' || { echo "번들 밖의 libmpv 를 사용했습니다 (Homebrew 의존)"; exit 1; }
ditto -c -k --keepParent "$APP" "dist/ClassPlayer-mac-$(uname -m).zip"
du -sh "$APP" dist/*.zip
echo "완료: $APP"
