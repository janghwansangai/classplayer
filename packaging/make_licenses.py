"""오픈소스 라이선스 고지 만들기.

앱에 함께 넣는 구성 요소(파이썬 패키지 + ffmpeg·mpv·deno·bgutil 실행 파일)의 이름·버전·라이선스·소스 주소를
NOTICE.md 로 정리하고, 각 라이선스 전문을 src/classplayer/assets/licenses/ 에 모은다.
버전은 설치된 패키지에서 읽으므로 의존성을 올린 뒤 다시 돌리면 된다 (빌드 때 CI 가 자동 실행).

실행: uv run python packaging/make_licenses.py
"""
from __future__ import annotations

import importlib.metadata as md
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "classplayer" / "assets" / "licenses"
TEXTS = ROOT / "packaging" / "license_texts"     # 패키지에 없는 전문 (GPL-3.0, LGPL-3.0, Deno, Python)
SITE = ROOT / "site" / "public" / "licenses.html"  # 소개 사이트의 '오픈소스 라이선스' 페이지 (같은 내용)

# (배포 이름, 화면 이름, 하는 일, 소스·홈페이지)
PY_PACKAGES = [
    ("PySide6_Essentials", "Qt 6 / PySide6", "화면(창·버튼) 그리기", "https://code.qt.io/cgit/pyside/pyside-setup.git/"),
    ("shiboken6", "Shiboken6", "PySide6 연결", "https://code.qt.io/cgit/pyside/pyside-setup.git/"),
    ("python-mpv", "python-mpv", "mpv 재생 엔진 연결", "https://github.com/jaseg/python-mpv"),
    ("yt-dlp", "yt-dlp", "유튜브 주소 풀기·내려받기", "https://github.com/yt-dlp/yt-dlp"),
    ("yt-dlp-ejs", "yt-dlp-ejs", "유튜브 JS 계산 도우미", "https://github.com/yt-dlp/ejs"),
    ("mutagen", "mutagen", "음악 파일 정보(yt-dlp)", "https://github.com/quodlibet/mutagen"),
    ("PyOpenGL", "PyOpenGL", "360도 화면 그리기", "https://github.com/mcfletch/pyopengl"),
    ("curl_cffi", "curl_cffi", "유튜브 접속(브라우저 흉내)", "https://github.com/lexiforest/curl_cffi"),
    ("cffi", "cffi", "C 라이브러리 연결", "https://github.com/python-cffi/cffi"),
    ("pycparser", "pycparser", "cffi 보조", "https://github.com/eliben/pycparser"),
    ("truststore", "truststore", "운영체제 인증서로 HTTPS 확인", "https://github.com/sethmlarson/truststore"),
    ("certifi", "certifi", "HTTPS 인증서 목록", "https://github.com/certifi/python-certifi"),
    ("Brotli", "Brotli", "압축 해제", "https://github.com/google/brotli"),
    ("pycryptodomex", "PyCryptodome", "암호 계산(yt-dlp)", "https://github.com/Legrandin/pycryptodome"),
    ("websockets", "websockets", "실시간 연결(yt-dlp)", "https://github.com/python-websockets/websockets"),
    ("requests", "requests", "HTTP(yt-dlp)", "https://github.com/psf/requests"),
    ("urllib3", "urllib3", "HTTP", "https://github.com/urllib3/urllib3"),
    ("idna", "idna", "국제 도메인 이름", "https://github.com/kjd/idna"),
    ("charset-normalizer", "charset-normalizer", "글자 인코딩 판별", "https://github.com/jawah/charset_normalizer"),
]

# 실행 파일·라이브러리 (이름, 라이선스, 하는 일, 소스, 전문 파일)
BINARIES = [
    ("Python 3.12", "PSF-2.0", "프로그램 실행", "https://www.python.org/downloads/source/", "Python-PSF.txt"),
    ("mpv / libmpv", "GPL-2.0-or-later", "영상·소리 재생 엔진", "https://github.com/mpv-player/mpv", "GPL-2.0.txt"),
    ("FFmpeg (ffmpeg, ffprobe, libav*)", "GPL-3.0-or-later (x264·x265 등 GPL 구성 요소 포함 빌드)",
     "코덱, 합치기·녹화·미리보기", "https://ffmpeg.org/download.html#get-sources", "GPL-3.0.txt"),
    ("Deno", "MIT", "유튜브 JS 계산", "https://github.com/denoland/deno", "Deno-MIT.txt"),
    ("bgutil-ytdlp-pot-provider-rs (bgutil-pot + yt-dlp 플러그인)", "GPL-3.0-or-later",
     "유튜브 PO 토큰", "https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs", "GPL-3.0.txt"),
    ("PyInstaller 부트로더", "GPL-2.0-or-later + 배포 예외(묶은 앱은 어떤 라이선스로든 배포 가능)",
     "실행 파일 시작", "https://github.com/pyinstaller/pyinstaller", "PyInstaller.txt"),
]


def license_of(dist: md.Distribution) -> str:
    m = dist.metadata
    lic = m.get("License-Expression") or ""
    if not lic or len(lic) > 80:
        cls = [c.split("::")[-1].strip() for c in (m.get_all("Classifier") or []) if c.startswith("License")]
        lic = " / ".join(cls) or (m.get("License") or "").splitlines()[0][:80]
    return lic


def copy_license_files(dist: md.Distribution, name: str) -> list[str]:
    out = []
    for f in dist.files or []:
        s = str(f)
        base = Path(s).name.upper()
        if ".DIST-INFO" not in s.upper() or not (base.startswith(("LICEN", "COPYING", "NOTICE"))):
            continue
        src = Path(dist.locate_file(f))
        if src.is_file():
            dst = OUT / f"{name}-{Path(s).name}"
            if not dst.suffix:
                dst = dst.with_suffix(".txt")
            shutil.copyfile(src, dst)
            out.append(dst.name)
    return out


def main() -> int:
    # 윈도우 콘솔(cp1252 등)에서도 한글·기호를 찍다가 죽지 않게 (v0.5.8 윈도우 빌드가 '→' 때문에 실패)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for p in TEXTS.glob("*.txt"):
        shutil.copyfile(p, OUT / p.name)
    # PyInstaller 부트로더 라이선스(예외 조항 포함)는 패키지에서 가져온다
    try:
        pi = md.distribution("pyinstaller")
        for f in pi.files or []:
            if Path(str(f)).name == "COPYING.txt":
                shutil.copyfile(Path(pi.locate_file(f)), OUT / "PyInstaller.txt")
    except md.PackageNotFoundError:
        pass

    rows, missing = [], []
    for dist_name, label, role, url in PY_PACKAGES:
        try:
            d = md.distribution(dist_name)
        except md.PackageNotFoundError:
            missing.append(dist_name)
            continue
        files = copy_license_files(d, label.split(" ")[0].replace("/", ""))
        lic = license_of(d)
        if "LGPL-3.0" in lic:
            files.append("LGPL-3.0.txt")
        rows.append((label, d.version, lic, role, url, files))

    lines = [
        "# ClassPlayer 오픈소스 라이선스 고지",
        "",
        "ClassPlayer 는 아래 오픈소스 소프트웨어를 함께 담아 배포합니다. 각 구성 요소는 표에 적힌 라이선스를 따르며,",
        "라이선스 전문은 이 폴더(도움말 › 오픈소스 라이선스)에 들어 있습니다.",
        "",
        "## 소스 코드",
        "",
        "- ClassPlayer 자체 소스: https://github.com/janghwansangai/classplayer",
        "- GPL·LGPL 구성 요소의 소스 코드는 아래 '소스' 주소에서 받을 수 있습니다. 이 앱에 넣은 바로 그 버전의 소스가",
        "  필요하면 위 ClassPlayer 저장소의 이슈로 요청해 주세요.",
        "- Qt(LGPL-3.0)는 동적 라이브러리로 들어 있어, 같은 버전의 다른 Qt 라이브러리로 바꿔 쓸 수 있습니다.",
        "",
        "## 실행 파일·라이브러리",
        "",
        "| 구성 요소 | 라이선스 | 하는 일 | 소스 | 전문 |",
        "|---|---|---|---|---|",
    ]
    for name, lic, role, url, txt in BINARIES:
        lines.append(f"| {name} | {lic} | {role} | {url} | {txt} |")
    lines += ["", "## 파이썬 패키지", "", "| 구성 요소 | 버전 | 라이선스 | 하는 일 | 소스 | 전문 |", "|---|---|---|---|---|---|"]
    for label, ver, lic, role, url, files in rows:
        lines.append(f"| {label} | {ver} | {lic} | {role} | {url} | {', '.join(files) or '-'} |")
    lines += ["", "FFmpeg·mpv 와 함께 들어 있는 코덱·자막 라이브러리(x264, x265, dav1d, libass, freetype 등)는 각자의",
              "라이선스를 따르며, 소스는 FFmpeg·mpv 빌드 안내(위 주소)에서 찾을 수 있습니다.", ""]
    (OUT / "NOTICE.md").write_text("\n".join(lines), encoding="utf-8")
    write_site(rows)
    print(f"licenses: {len(list(OUT.iterdir()))} files -> {OUT.relative_to(ROOT)}")
    if missing:
        print("설치되지 않은 패키지(건너뜀):", ", ".join(missing))
    return 0


def write_site(rows) -> None:
    """소개 사이트용 HTML (앱과 같은 표). 사이트 배포는 따로: cd site && npx wrangler deploy"""
    from html import escape as e

    def link(u: str) -> str:
        return f'<a href="{e(u)}">{e(u.replace("https://", ""))}</a>'

    bin_rows = "\n".join(f"<tr><td>{e(n)}</td><td>{e(lic)}</td><td>{e(role)}</td><td>{link(url)}</td></tr>"
                         for n, lic, role, url, _t in BINARIES)
    py_rows = "\n".join(f"<tr><td>{e(label)}</td><td>{e(ver)}</td><td>{e(lic)}</td><td>{e(role)}</td><td>{link(url)}</td></tr>"
                        for label, ver, lic, role, url, _f in rows)
    html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>오픈소스 라이선스 · ClassPlayer</title>
<link rel="icon" href="icon.svg" type="image/svg+xml">
<style>
  :root {{ --bg:#141518; --panel:#1b1c21; --line:#2a2b31; --text:#e7e8ec; --muted:#9a9ca6; --amber2:#fbbf24; }}
  body {{ margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,"Apple SD Gothic Neo","Pretendard","Malgun Gothic",system-ui,sans-serif; line-height:1.6; }}
  a {{ color:var(--amber2); word-break:break-all; }}
  .wrap {{ max-width:1000px; margin:0 auto; padding:32px 16px 56px; }}
  h1 {{ font-size:clamp(24px,4vw,34px); margin:8px 0 12px; }}
  h2 {{ font-size:20px; margin:32px 0 10px; }}
  p, li {{ color:var(--muted); }}
  .table {{ overflow-x:auto; border:1px solid var(--line); border-radius:12px; }}
  table {{ border-collapse:collapse; width:100%; min-width:640px; font-size:14px; }}
  th, td {{ text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ background:var(--panel); }}
  tr:last-child td {{ border-bottom:none; }}
</style>
</head>
<body>
<div class="wrap">
  <a href="./">← ClassPlayer 소개로</a>
  <h1>오픈소스 라이선스</h1>
  <p>ClassPlayer 는 아래 오픈소스 소프트웨어를 함께 담아 배포합니다. 각 구성 요소는 표에 적힌 라이선스를 따릅니다.
  라이선스 전문은 앱의 <b>도움말 › 오픈소스 라이선스</b>와
  <a href="https://github.com/janghwansangai/classplayer/tree/main/src/classplayer/assets/licenses">GitHub</a>에 있습니다.</p>
  <h2>소스 코드</h2>
  <ul>
    <li>ClassPlayer 자체 소스: <a href="https://github.com/janghwansangai/classplayer">github.com/janghwansangai/classplayer</a></li>
    <li>GPL·LGPL 구성 요소의 소스 코드는 아래 주소에서 받을 수 있습니다. 앱에 넣은 바로 그 버전의 소스가 필요하면 ClassPlayer 저장소의 이슈로 요청해 주세요.</li>
    <li>Qt(LGPL-3.0)는 동적 라이브러리로 들어 있어, 같은 버전의 다른 Qt 라이브러리로 바꿔 쓸 수 있습니다.</li>
  </ul>
  <h2>실행 파일·라이브러리</h2>
  <div class="table"><table>
    <tr><th>구성 요소</th><th>라이선스</th><th>하는 일</th><th>소스</th></tr>
{bin_rows}
  </table></div>
  <h2>파이썬 패키지</h2>
  <div class="table"><table>
    <tr><th>구성 요소</th><th>버전</th><th>라이선스</th><th>하는 일</th><th>소스</th></tr>
{py_rows}
  </table></div>
  <p>FFmpeg·mpv 와 함께 들어 있는 코덱·자막 라이브러리(x264, x265, dav1d, libass, freetype 등)는 각자의 라이선스를 따르며,
  소스는 FFmpeg·mpv 빌드 안내(위 주소)에서 찾을 수 있습니다. YouTube는 Google LLC의 상표입니다.</p>
</div>
</body>
</html>
"""
    if SITE.parent.is_dir():
        SITE.write_text(html, encoding="utf-8")
        print(f"site: {SITE.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
