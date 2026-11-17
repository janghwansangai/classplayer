"""이용 안내(처음 실행 때 한 번 확인)와 오픈소스 라이선스 창."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QPushButton, QSplitter,
                               QTextBrowser, QVBoxLayout)

from classplayer.bootstrap import bundled_dir

NOTICE_VERSION = 1      # 안내 내용을 크게 바꾸면 올린다 → 모두에게 한 번 더 보여 줌
CREATOR = "다있쌤 로디"
DOWNLOAD_SITE = "https://hwansang.kr/tools"
SOURCE_URL = "https://github.com/janghwansangai/classplayer"
APP_LICENSE = "GNU GPL v3.0 또는 그 이후 버전 (GPL-3.0-or-later)"

NOTICE_HTML = """
<h2 style="margin-top:0">ClassPlayer 이용 안내</h2>
<p>ClassPlayer 는 <b>수업 준비를 돕기 위해 선생님이 만든 무료·비영리 영상 플레이어</b>입니다.
광고·유료 기능·개인정보 수집이 없습니다.</p>

<h3>저작권</h3>
<ul>
<li>영상 미리 받기·녹화·녹음은 <b>수업 목적에 필요한 범위</b>에서만 써 주세요
(저작권법 제25조 '학교교육 목적 등에의 이용'). 개인·가정용이 아닌 교실 상영에는 '사적 이용' 예외가 적용되지 않습니다.</li>
<li>받거나 녹화한 영상을 <b>학생·학부모에게 나눠 주거나 인터넷에 다시 올리지 마세요.</b>
원격 수업으로 보낼 때는 내려받기·복제를 막는 조치가 필요합니다.</li>
<li>불법으로 올라온 영상은 받지 말고, 저작권자가 정한 이용 조건을 지켜 주세요.</li>
</ul>

<h3>유튜브 이용약관</h3>
<p>유튜브 영상을 파일로 받거나 광고 없이 보는 기능은 유튜브 이용약관과 다를 수 있습니다.
이 기능을 쓰는 책임은 사용하는 분께 있으며, 학교·교육청 지침이 있다면 그에 따라 주세요.</p>

<h3>개인정보·인터넷 접속</h3>
<ul>
<li>ClassPlayer 는 이름·이메일·시청 기록 등을 모으거나 보내지 않습니다.</li>
<li>인터넷은 영상 재생·검색·미리 받기, 협찬 구간 정보(SponsorBlock), 새 버전 확인(GitHub)에만 씁니다.</li>
<li>'자동 받기'의 좋아요 목록 기능을 켤 때만, 고른 브라우저의 유튜브 로그인 정보를 그 순간 읽고 저장하지 않습니다.</li>
<li>'오류 신고'는 누를 때만 앱 버전과 화면 이름(파일 이름·링크 제외)을 보냅니다.</li>
</ul>

<h3>오픈소스</h3>
<p>FFmpeg, mpv, Qt, yt-dlp 등 오픈소스 소프트웨어를 함께 담고 있습니다.
각각의 라이선스는 <b>도움말 › 오픈소스 라이선스</b>에서 볼 수 있습니다.</p>
"""


def licenses_dir() -> Path:
    return (bundled_dir() or Path(__file__).resolve().parent.parent) / "assets" / "licenses"


class NoticeDialog(QDialog):
    """first_run=True 면 '확인' 체크 후 시작, 아니면 읽기만."""

    def __init__(self, parent=None, first_run: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("ClassPlayer 이용 안내")
        self.setMinimumSize(620, 600)
        body = QTextBrowser()
        body.setOpenExternalLinks(True)
        body.setHtml(NOTICE_HTML)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)
        lay.addWidget(body, 1)
        btns = QHBoxLayout()
        if first_run:
            self.agree = QCheckBox("위 내용을 이해했고, 수업 목적 범위에서 사용하겠습니다")
            b_quit = QPushButton("끝내기")
            b_start = QPushButton("시작하기")
            b_start.setObjectName("primary")
            b_start.setEnabled(False)
            self.agree.toggled.connect(b_start.setEnabled)
            b_quit.clicked.connect(self.reject)
            b_start.clicked.connect(self.accept)
            lay.addWidget(self.agree)
            btns.addStretch(1)
            btns.addWidget(b_quit)
            btns.addWidget(b_start)
        else:
            b_lic = QPushButton("오픈소스 라이선스…")
            b_lic.clicked.connect(lambda: LicenseDialog(self).exec())
            b_close = QPushButton("닫기")
            b_close.setObjectName("primary")
            b_close.clicked.connect(self.accept)
            btns.addWidget(b_lic)
            btns.addStretch(1)
            btns.addWidget(b_close)
        lay.addLayout(btns)


class AboutDialog(QDialog):
    """도움말 › ClassPlayer 정보: 버전, 제작자, 다운로드 사이트, 라이선스."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from classplayer import __version__
        self.setWindowTitle("ClassPlayer 정보")
        self.setMinimumWidth(460)
        icon = QLabel()
        pm = QPixmap(str((bundled_dir() or Path(__file__).resolve().parent.parent) / "assets" / "icon.png"))
        if not pm.isNull():
            icon.setPixmap(pm.scaled(88, 88, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel(f"<div style='font-size:22px;font-weight:800'>ClassPlayer</div>"
                       f"<div style='color:#8b8e99'>버전 {__version__} · 수업용 영상 플레이어</div>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info = QLabel(
            "<table cellspacing='0' cellpadding='5'>"
            f"<tr><td style='color:#8b8e99'>제작자</td><td><b>{CREATOR}</b></td></tr>"
            f"<tr><td style='color:#8b8e99'>다운로드 사이트</td><td><a href='{DOWNLOAD_SITE}'>{DOWNLOAD_SITE}</a></td></tr>"
            f"<tr><td style='color:#8b8e99'>라이선스</td><td>{APP_LICENSE}</td></tr>"
            f"<tr><td style='color:#8b8e99'>소스 코드</td><td><a href='{SOURCE_URL}'>{SOURCE_URL.replace('https://', '')}</a></td></tr>"
            "</table>")
        info.setOpenExternalLinks(True)
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note = QLabel("이 프로그램은 자유 소프트웨어입니다. GNU GPL 3판(또는 그 이후 판)에 따라 다시 배포하거나 고칠 수 있으며, "
                      "어떠한 보증도 하지 않습니다. 함께 담긴 오픈소스 구성 요소는 각자의 라이선스를 따릅니다.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        b_site = QPushButton("다운로드 사이트 열기")
        b_site.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DOWNLOAD_SITE)))
        b_lic = QPushButton("오픈소스 라이선스…")
        b_lic.clicked.connect(lambda: LicenseDialog(self).exec())
        b_close = QPushButton("닫기")
        b_close.setObjectName("primary")
        b_close.clicked.connect(self.accept)
        btns = QHBoxLayout()
        btns.addWidget(b_site)
        btns.addWidget(b_lic)
        btns.addStretch(1)
        btns.addWidget(b_close)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(12)
        lay.addWidget(icon)
        lay.addWidget(title)
        lay.addWidget(info)
        lay.addWidget(note)
        lay.addLayout(btns)


class LicenseDialog(QDialog):
    """왼쪽 파일 목록(고지 + 라이선스 전문), 오른쪽 내용."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("오픈소스 라이선스")
        self.resize(980, 680)
        self.dir = licenses_dir()
        files = sorted(p.name for p in self.dir.glob("*") if p.is_file() and p.name != "NOTICE.md") \
            if self.dir.is_dir() else []
        self.list = QListWidget()
        self.list.addItem("고지 (먼저 읽기)")
        self.list.addItems(files)
        self.list.setMaximumWidth(280)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.list.currentRowChanged.connect(self._show)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.list)
        split.addWidget(self.view)
        split.setSizes([240, 740])
        b_folder = QPushButton("폴더 열기")
        b_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.dir))))
        b_close = QPushButton("닫기")
        b_close.setObjectName("primary")
        b_close.clicked.connect(self.accept)
        btns = QHBoxLayout()
        btns.addWidget(QLabel("ClassPlayer 소스: github.com/janghwansangai/classplayer"))
        btns.addStretch(1)
        btns.addWidget(b_folder)
        btns.addWidget(b_close)
        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        lay.addLayout(btns)
        self.list.setCurrentRow(0)

    def _show(self, row: int) -> None:
        if row < 0:
            return
        if row == 0:
            p = self.dir / "NOTICE.md"
            text = p.read_text(encoding="utf-8") if p.exists() else "라이선스 고지 파일을 찾지 못했습니다."
            self.view.setMarkdown(text)
            return
        p = self.dir / self.list.item(row).text()
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            text = str(e)
        self.view.setPlainText(text)
