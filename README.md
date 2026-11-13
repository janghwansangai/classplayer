# ClassPlayer — 수업용 영상 플레이어

초등학교 수업에서 안심하고 쓸 수 있는 영상 플레이어입니다.

- **모든 코덱 무료 재생**: HEVC(H.265), AV1, VP9, VVC까지 추가 구매 없이 재생 (FFmpeg/libmpv 기반)
- **대용량 고화질 빠른 재생**: 맥은 VideoToolbox, 윈도우는 D3D11 하드웨어 디코딩. 4K/8K 지원
- **유튜브 링크를 넣으면 영상만 재생**: 광고, 추천 영상, 댓글, 다음 영상 자동재생이 모두 없음. 영상 안 협찬 구간은 SponsorBlock 데이터로 자동 건너뜀
- **유튜브 다운로드**: 수업 전에 미리 받아 두면 인터넷이 끊겨도 안전. 1080p MP4(호환), 최고 화질(4K/8K MKV), 소리만(MP3), 자막 동시 저장
- **360도 영상**: 마우스 드래그로 둘러보기, 휠/트랙패드 핀치로 확대. 확대 시 바이큐빅 표본화로 뭉개짐을 줄이고, 화질 보호 확대 한계(기본 3배) 제공. 일반 360도 파일(등장방형)과 유튜브 EAC 큐브맵, 상하/좌우 분할 입체 모두 지원
- **라이브러리 검색**: 폴더를 등록하면 파일명으로 바로 검색

## 바로 받기

설치 없이 쓰려면 [릴리스 페이지](https://github.com/janghwansangai/classplayer/releases)에서 받으세요.

- 맥(Apple Silicon): `ClassPlayer-mac-arm64.zip` → 풀어서 `ClassPlayer.app`을 응용 프로그램 폴더로. 처음 한 번은 **우클릭 > 열기**.
- 윈도우: `ClassPlayer-windows-x64.zip` → 풀어서 `ClassPlayer.exe` 실행. SmartScreen 경고가 뜨면 **추가 정보 > 실행**.

두 파일 모두 GitHub Actions가 자동으로 빌드합니다 (`v*` 태그를 올리면 릴리스에 붙습니다).

## 요구 사항 (맥, 소스로 실행할 때)

```bash
brew install mpv ffmpeg deno
```

- `mpv`: 재생 엔진(libmpv). `ffmpeg`: 유튜브 영상/소리 합치기와 360도 메타데이터 읽기.
- `deno`(또는 `node`): 유튜브가 요구하는 자바스크립트 계산용. 없으면 일부 화질이 빠질 수 있습니다.
- Python 3.12와 [uv](https://docs.astral.sh/uv/)가 필요합니다. `brew install uv`

## 설치와 실행

```bash
uv sync
```

```bash
uv run classplayer
```

파일을 바로 열려면 `uv run classplayer /경로/영상.mp4` 또는 유튜브 링크를 인자로 줍니다.

## 사용법

| 하고 싶은 것 | 방법 |
|---|---|
| 유튜브 바로 재생 | 위쪽 입력창에 링크를 붙여넣고 Enter |
| 수업 전 미리 받기 | 링크 입력 후 **다운로드…** 버튼 (⌘D). 받은 파일은 라이브러리에 자동 표시 |
| 파일 재생 | **파일 열기…** (⌘O), 창에 드래그, 또는 라이브러리에서 더블클릭 |
| 360도 모드 | 2:1 비율 파일은 자동으로 켜짐. 수동은 `3` 키 또는 하단 **360°** 버튼. 유튜브 360도 영상은 켜면 자동으로 EAC 투영 |
| 360도 조작 | 드래그로 회전, 휠/핀치로 확대, `R`로 시점 초기화, `+`/`-` 확대 |
| 전체화면 | `F` 또는 더블클릭, `Esc`로 해제 (컨트롤은 자동 숨김) |
| 라이브러리 폴더 추가 | 왼쪽 패널의 `+` 또는 파일 메뉴 |

단축키 전체 목록은 **도움말 > 단축키**에 있습니다.

### 설정 메뉴
- 유튜브 스트리밍 최대 화질 (기본 1080p, 360도 영상은 4K 이상으로 올리세요)
- 360도 확대 한계 (2배/3배/4배/무제한), 고품질 표본화 켜기/끄기
- 협찬 구간 자동 건너뛰기 켜기/끄기
- 다운로드 폴더

## 수업 전 권장 흐름

1. 전날 링크를 **다운로드…**로 받아 둡니다(1080p MP4 기본).
2. 수업 때는 라이브러리에서 검색해 더블클릭합니다. 인터넷이 필요 없고, 광고·추천 영상이 나올 일이 없습니다.
3. 급할 때만 링크 바로 재생을 씁니다. 유튜브 측 차단 정책이 바뀌면 바로 재생이 실패할 수 있으니, 그때는 `uv sync -U`로 yt-dlp를 최신으로 올리세요.

## 배포용 앱 만들기 (패키징)

### 맥 `.app`

```bash
./packaging/build_mac.sh
```

`dist/ClassPlayer.app`과 배포용 `dist/ClassPlayer-mac-arm64.zip`이 만들어집니다. libmpv, ffmpeg, ffprobe, deno가 모두 안에 들어 있어 받는 쪽 맥에는 아무것도 설치할 필요가 없습니다. 빌드 끝에 영상을 실제로 열어 보는 자가 점검이 자동으로 돌아갑니다.

다른 맥에서 처음 열 때는 Apple 개발자 서명이 없어서 "확인되지 않은 개발자" 경고가 뜹니다. 앱을 **우클릭 > 열기**로 한 번 열면 그 다음부터는 그냥 열립니다. 경고를 없애려면 Apple 개발자 계정(연 99달러)으로 서명·공증해야 하며, `CODESIGN_IDENTITY` 환경변수에 인증서 이름을 넣고 빌드하면 서명까지 됩니다.

주의: 이 빌드는 Apple Silicon(M1 이후) 전용입니다. 인텔 맥용은 인텔 맥에서 같은 스크립트로 빌드해야 합니다.

### 윈도우 `.exe`

윈도우 컴퓨터에서 `packaging\build_windows.bat`를 실행합니다. 스크립트 머리에 사전 준비(Python, uv, ffmpeg, deno, libmpv-2.dll)가 적혀 있습니다. `dist\ClassPlayer\` 폴더 전체를 압축해 배포합니다. 윈도우 빌드는 아직 실제로 돌려보지 않았습니다.

## 윈도우에서 소스로 실행 (아직 검증 전)

1. Python 3.12, uv, ffmpeg(`winget install ffmpeg`), deno(`winget install DenoLand.Deno`)를 설치합니다.
2. libmpv를 받습니다: https://github.com/shinchiro/mpv-winbuild-cmake/releases 에서 `mpv-dev-x86_64-...7z`를 풀어 `libmpv-2.dll`을 프로젝트의 `bin\` 폴더나 PATH에 둡니다.
3. `uv sync` 후 `uv run classplayer`.

## 개발과 검증

```bash
uv run pytest -q tests/test_units.py
```

```bash
tests/make_assets.sh
```

테스트 영상(1080p H.264, 4K HEVC, 라벨 붙은 360도 등장방형·EAC·상하분할)을 만든 뒤 아래로 실제 화면을 캡처해 확인합니다.

```bash
uv run python tests/smoke.py tests/assets/test_360_eac.mp4 /tmp/smoke_out eac
```

```bash
uv run python tests/perf.py tests/assets/test_hevc_4k.mp4 /tmp/perf_out 8
```

`perf.py`는 N초 재생 뒤 하드웨어 디코더 이름과 드롭 프레임 수를 출력합니다.

## 구조

```
src/classplayer/
  app.py              메인 창, 메뉴, 단축키, 다운로드 패널, 전체화면
  bootstrap.py        libmpv 경로, 외부 도구(yt-dlp, ffmpeg) 찾기
  settings.py         QSettings 설정
  player/mpv_widget.py  libmpv OpenGL 렌더 위젯 (일반 + 360도)
  player/controls.py    하단 컨트롤바
  vr/shaders.py       360도 투영 GLSL (등장방형, EAC, 바이큐빅)
  vr/view.py          시점 상태와 화질 보호 확대 한계
  youtube/download.py yt-dlp 다운로드 작업
  youtube/sponsorblock.py 협찬 구간 건너뛰기
  youtube/probe.py    ffprobe로 360도 메타데이터 읽기
  library/library.py  폴더 스캔과 검색 패널
```

## 알려진 제한과 다음 단계

- 맥 `.app`은 만들어지지만 Apple 서명·공증은 없습니다. 윈도우 `.exe`는 스크립트만 있고 빌드는 아직 해보지 않았습니다.
- 윈도우 빌드는 아직 실제로 돌려보지 않았습니다.
- 360도 모드에서는 자막이 구면에 함께 투영되어 휘어 보입니다.
- 유튜브 다운로드와 광고 없는 재생은 유튜브 약관상 허용되지 않으므로 앱스토어 배포는 불가능하며, 수업 목적 범위에서 사용하세요.
