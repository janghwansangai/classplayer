# ClassPlayer 작업 메모 (AI 세션용)

- 목적: 초등 수업용 영상 플레이어. 유튜브 UI 없이 영상만, 수업 전 미리 받기, 360도, 모든 코덱.
- 스택: Python 3.12 + PySide6 + python-mpv(libmpv) + yt-dlp[default] + PyOpenGL. uv로 관리 (`uv sync`, `uv run classplayer`).
- UI 문구는 한국어. 코드 주석도 한국어 위주.
- 재생 엔진 설정은 `player/mpv_widget.py`의 MPV() 옵션 dict 한 곳에 모아 둔다. 360도 셰이더는 `vr/shaders.py`.
- 검증: `uv run pytest -q tests/test_units.py` → `tests/make_assets.sh` → `tests/smoke.py`(화면 캡처 PNG를 직접 보고 방향 확인) / `tests/perf.py`(드롭 프레임, hwdec 이름).
- EAC 큐브맵 좌표는 FFmpeg v360의 eac 입력 규약을 따르며, 2026-11-13 라벨 영상으로 6방향 검증 완료. 바꿀 때는 반드시 smoke 캡처로 다시 확인.
- 맥에서 QOpenGLWidget + mpv 렌더 API: 일반 모드는 flip_y=True, 360도 FBO 경로는 flip_y=False + 셰이더 u_flipY=0 이 맞는 조합.
- 아직 없는 것: PyInstaller 패키징, 윈도우 실기 검증, PO 토큰 제공 플러그인, 초해상도(FSRCNNX) 셰이더.
- 유튜브 재생 경로: `youtube/stream.py`의 StreamResolver가 앱 안에서 yt-dlp Python API로 주소를 풀어 `MpvWidget.play_stream()`에 넘긴다(기본). mpv 내장 ytdl 훅은 실패 시 폴백인데, 2026-11-13 기준 훅 경로는 googlevideo 403이 났고 내부 풀기 경로는 정상이었다.
- 성능 튜닝용 환경변수: `CLASSPLAYER_SWAP_INTERVAL`(기본 1), `CLASSPLAYER_BLOCK_TARGET`(기본 1). 로컬 4K60 HEVC는 어느 조합이든 드롭 0~3.
- 테스트: `CLASSPLAYER_TEST_MAX_HEIGHT=1080 uv run python tests/perf.py <유튜브링크> /tmp/out 24` 로 스트리밍 드롭 측정.
- 패키징: `./packaging/build_mac.sh` → `dist/ClassPlayer.app` (+zip). spec은 `packaging/classplayer.spec`. 번들에는 libmpv(+libav 의존성), ffmpeg, ffprobe, deno가 들어간다. 자가 점검은 `CLASSPLAYER_SELFTEST=<파일|링크>` 환경변수로 앱을 띄우면 6초 뒤 JSON 한 줄 출력 후 종료. 번들 실행 시 bootstrap이 Homebrew 경로를 제외하고, build 스크립트가 `libmpv.dylib` 링크를 만든다(python-mpv가 그 이름으로 찾음).
- 윈도우 빌드: `packaging/build_windows.bat` (미검증).
