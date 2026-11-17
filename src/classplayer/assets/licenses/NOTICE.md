# ClassPlayer 오픈소스 라이선스 고지

ClassPlayer 는 아래 오픈소스 소프트웨어를 함께 담아 배포합니다. 각 구성 요소는 표에 적힌 라이선스를 따르며,
라이선스 전문은 이 폴더(도움말 › 오픈소스 라이선스)에 들어 있습니다.

## 소스 코드

- ClassPlayer 자체: GNU GPL v3.0 또는 그 이후 버전(GPL-3.0-or-later, 전문 GPL-3.0.txt). 제작자 다있쌤 로디.
- ClassPlayer 자체 소스: https://github.com/janghwansangai/classplayer
- GPL·LGPL 구성 요소의 소스 코드는 아래 '소스' 주소에서 받을 수 있습니다. 이 앱에 넣은 바로 그 버전의 소스가
  필요하면 위 ClassPlayer 저장소의 이슈로 요청해 주세요.
- Qt(LGPL-3.0)는 동적 라이브러리로 들어 있어, 같은 버전의 다른 Qt 라이브러리로 바꿔 쓸 수 있습니다.

## 실행 파일·라이브러리

| 구성 요소 | 라이선스 | 하는 일 | 소스 | 전문 |
|---|---|---|---|---|
| Python 3.12 | PSF-2.0 | 프로그램 실행 | https://www.python.org/downloads/source/ | Python-PSF.txt |
| mpv / libmpv | GPL-2.0-or-later | 영상·소리 재생 엔진 | https://github.com/mpv-player/mpv | GPL-2.0.txt |
| FFmpeg (ffmpeg, ffprobe, libav*) | GPL-3.0-or-later (x264·x265 등 GPL 구성 요소 포함 빌드) | 코덱, 합치기·녹화·미리보기 | https://ffmpeg.org/download.html#get-sources | GPL-3.0.txt |
| Deno | MIT | 유튜브 JS 계산 | https://github.com/denoland/deno | Deno-MIT.txt |
| bgutil-ytdlp-pot-provider-rs (bgutil-pot + yt-dlp 플러그인) | GPL-3.0-or-later | 유튜브 PO 토큰 | https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs | GPL-3.0.txt |
| PyInstaller 부트로더 | GPL-2.0-or-later + 배포 예외(묶은 앱은 어떤 라이선스로든 배포 가능) | 실행 파일 시작 | https://github.com/pyinstaller/pyinstaller | PyInstaller.txt |

## 파이썬 패키지

| 구성 요소 | 버전 | 라이선스 | 하는 일 | 소스 | 전문 |
|---|---|---|---|---|---|
| Qt 6 / PySide6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | 화면(창·버튼) 그리기 | https://code.qt.io/cgit/pyside/pyside-setup.git/ | LGPL-3.0.txt |
| Shiboken6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | PySide6 연결 | https://code.qt.io/cgit/pyside/pyside-setup.git/ | LGPL-3.0.txt |
| python-mpv | 1.0.8 | GNU General Public License v2 or later (GPLv2+) / GNU Lesser General Public License v2 or later (LGPLv2+) | mpv 재생 엔진 연결 | https://github.com/jaseg/python-mpv | python-mpv-LICENSE.GPL, python-mpv-LICENSE.LGPL |
| yt-dlp | 2026.10.26 | Unlicense | 유튜브 주소 풀기·내려받기 | https://github.com/yt-dlp/yt-dlp | yt-dlp-LICENSE.txt |
| yt-dlp-ejs | 0.8.0 | Unlicense AND MIT AND ISC | 유튜브 JS 계산 도우미 | https://github.com/yt-dlp/ejs | yt-dlp-ejs-LICENSE.txt |
| mutagen | 1.48.1 | GPL-2.0-or-later | 음악 파일 정보(yt-dlp) | https://github.com/quodlibet/mutagen | mutagen-COPYING.txt |
| PyOpenGL | 3.1.10 | BSD License | 360도 화면 그리기 | https://github.com/mcfletch/pyopengl | - |
| curl_cffi | 0.17.0 | MIT | 유튜브 접속(브라우저 흉내) | https://github.com/lexiforest/curl_cffi | curl_cffi-LICENSE.txt |
| cffi | 2.1.1 | MIT-0 | C 라이브러리 연결 | https://github.com/python-cffi/cffi | cffi-LICENSE.txt |
| pycparser | 3.12 | BSD-3-Clause | cffi 보조 | https://github.com/eliben/pycparser | pycparser-LICENSE.txt |
| truststore | 0.10.5 | MIT | 운영체제 인증서로 HTTPS 확인 | https://github.com/sethmlarson/truststore | truststore-LICENSE.txt |
| certifi | 2026.10.23 | Mozilla Public License 2.0 (MPL 2.0) | HTTPS 인증서 목록 | https://github.com/certifi/python-certifi | certifi-LICENSE.txt |
| Brotli | 1.2.1 | MIT | 압축 해제 | https://github.com/google/brotli | Brotli-LICENSE.txt |
| PyCryptodome | 3.24.2 | BSD License / Public Domain | 암호 계산(yt-dlp) | https://github.com/Legrandin/pycryptodome | PyCryptodome-LICENSE.rst |
| websockets | 17.4 | BSD-3-Clause | 실시간 연결(yt-dlp) | https://github.com/python-websockets/websockets | websockets-LICENSE.txt |
| requests | 2.34.2 | Apache Software License | HTTP(yt-dlp) | https://github.com/psf/requests | requests-LICENSE.txt, requests-NOTICE.txt |
| urllib3 | 2.8.0 | MIT | HTTP | https://github.com/urllib3/urllib3 | urllib3-LICENSE.txt |
| idna | 3.20 | BSD-3-Clause | 국제 도메인 이름 | https://github.com/kjd/idna | idna-LICENSE.md |
| charset-normalizer | 3.5.2 | MIT | 글자 인코딩 판별 | https://github.com/jawah/charset_normalizer | charset-normalizer-LICENSE.txt |

FFmpeg·mpv 와 함께 들어 있는 코덱·자막 라이브러리(x264, x265, dav1d, libass, freetype 등)는 각자의
라이선스를 따르며, 소스는 FFmpeg·mpv 빌드 안내(위 주소)에서 찾을 수 있습니다.
