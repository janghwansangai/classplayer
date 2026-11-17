"""'자동 받기' 옆 창: 가져올 곳(유튜브 재생목록·좋아요)과 받을 목록(대기열)을 보여 주고 관리한다."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QRadioButton,
                               QSplitter, QToolButton, QVBoxLayout, QWidget)

from classplayer.library.autodownload import AutoDownloadStore, QueueItem, Source, browser_choices
from classplayer.ui import icons

STATUS = {"wait": ("대기", "#8b8e99"), "running": ("받는 중", "#fbbf24"), "done": ("완료", "#4ade80"),
          "failed": ("실패", "#f87171")}


def ago(ts: float) -> str:
    if not ts:
        return "아직 확인 안 함"
    d = int(time.time() - ts)
    if d < 60:
        return "방금 확인"
    if d < 3600:
        return f"{d // 60}분 전 확인"
    if d < 86400:
        return f"{d // 3600}시간 전 확인"
    return f"{d // 86400}일 전 확인"


class LikedDialog(QDialog):
    """좋아요·나중에 볼 동영상 가져오기: 목록 종류, 브라우저, 넣을 재생목록."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("유튜브 좋아요 · 나중에 볼 동영상 가져오기")
        self.setMinimumWidth(520)
        self.r_liked = QRadioButton("좋아요 표시한 동영상")
        self.r_later = QRadioButton("나중에 볼 동영상")
        self.r_liked.setChecked(True)
        kind = QHBoxLayout()
        kind.addWidget(self.r_liked)
        kind.addWidget(self.r_later)
        kind.addStretch(1)
        self.browser = QComboBox()
        for key, label in browser_choices():
            self.browser.addItem(label, key)
        self.playlist = QLineEdit("유튜브 좋아요")
        self.r_liked.toggled.connect(lambda on: self.playlist.setText("유튜브 좋아요" if on else "나중에 볼 동영상"))
        form = QFormLayout()
        form.addRow("목록", kind)
        form.addRow("로그인한 브라우저", self.browser)
        form.addRow("넣을 재생목록", self.playlist)
        note = QLabel("유튜브에 로그인해 둔 브라우저를 고르세요. 로그인 정보는 목록을 가져오는 순간에만 그 브라우저에서 읽고, "
                      "ClassPlayer 에는 저장하지 않습니다.<br>· 맥: 처음에 '키체인' 허용 창이 뜨면 비밀번호를 넣고 '허용'을 누르세요."
                      "<br>· 윈도우: 최신 크롬·엣지는 보안 정책상 읽을 수 없어 파이어폭스(또는 웨일)를 권합니다."
                      "<br>· 안 되면 좋아요한 영상을 내 재생목록(일부 공개)에 담고 '재생목록 링크'로 추가하세요.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("가져오기")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addWidget(bb)

    def values(self) -> tuple[str, str, str]:
        return ("liked" if self.r_liked.isChecked() else "watchlater", str(self.browser.currentData()),
                self.playlist.text().strip())


class AutoDownloadPanel(QWidget):
    add_playlist_link_requested = Signal()
    add_liked_requested = Signal()
    add_link_requested = Signal()
    check_now_requested = Signal()
    play_requested = Signal(str)
    reveal_requested = Signal(str)
    enabled_toggled = Signal(bool)
    pause_streaming_toggled = Signal(bool)

    def __init__(self, store: AutoDownloadStore, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("library")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.store = store

        def chip(text: str, tip: str, slot) -> QPushButton:
            b = QPushButton(text)
            b.setObjectName("chip")
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            return b

        # ---- 머리: 켜고 끄기 + 상태
        self.btn_on = QPushButton("")
        self.btn_on.setCheckable(True)
        self.btn_on.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_on.toggled.connect(self._on_toggle)
        title = QLabel("<b>자동 받기</b>")
        head = QHBoxLayout()
        head.addWidget(title, 1)
        head.addWidget(self.btn_on)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("muted")
        self.lbl_status.setWordWrap(True)

        # ---- 가져올 곳
        self.sources = QListWidget()
        self.sources.setToolTip("새 영상을 확인하는 곳(유튜브 재생목록·좋아요) · 오른쪽 클릭: 지금 확인·멈추기·빼기")
        self.sources.setObjectName("pllists")
        self.sources.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sources.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.sources.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sources.customContextMenuRequested.connect(self._source_menu)
        src_btns = QHBoxLayout()
        src_btns.setSpacing(6)
        src_btns.addWidget(chip("재생목록 링크", "유튜브 재생목록(공개·일부 공개) 링크를 넣으면 새 영상이 생길 때마다 받습니다",
                                lambda: self.add_playlist_link_requested.emit()))
        src_btns.addWidget(chip("좋아요·나중에 볼", "유튜브에 로그인한 브라우저에서 좋아요·나중에 볼 동영상 목록을 가져옵니다",
                                lambda: self.add_liked_requested.emit()))
        src_btns.addWidget(chip("지금 확인", "가져올 곳에 새 영상이 있는지 바로 확인", lambda: self.check_now_requested.emit()))
        src_box = QWidget()
        sl = QVBoxLayout(src_box)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(6)
        sl.addWidget(QLabel("<b>가져올 곳</b> <span style='color:#8b8e99'>— 새 영상이 생기면 자동으로 담음</span>"))
        sl.addWidget(self.sources, 1)
        sl.addLayout(src_btns)

        # ---- 받을 목록
        self.queue = QListWidget()
        self.queue.setToolTip("받을 목록 · 위에서부터 하나씩 받음 · 받은 영상은 두 번 눌러 재생 · 오른쪽 클릭: 다음 차례로·다시 받기·빼기")
        self.queue.setIconSize(QSize(14, 14))
        self.queue.setSpacing(1)
        self.queue.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.queue.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.queue.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.queue.customContextMenuRequested.connect(self._queue_menu)
        self.queue.itemDoubleClicked.connect(self._queue_activate)
        self.lbl_counts = QLabel("")
        q_btns = QHBoxLayout()
        q_btns.setSpacing(6)
        b_link = QToolButton()
        b_link.setIcon(QIcon(icons.pixmap("link", "#d9dbe3", 16)))
        b_link.setToolTip("유튜브 링크 담기…")
        b_link.clicked.connect(lambda: self.add_link_requested.emit())
        q_btns.addWidget(b_link)
        q_btns.addStretch(1)
        q_btns.addWidget(chip("실패 다시", "실패한 영상을 다시 받기", lambda: store.retry()))
        q_btns.addWidget(chip("완료 지우기", "다 받은 항목을 목록에서 지우기 (파일은 그대로)", lambda: store.clear_done()))
        self.pause_streaming = QCheckBox("유튜브 재생 중에는 쉬기 (수업 화면이 끊기지 않게)")
        self.pause_streaming.setToolTip("유튜브 영상을 보는 동안 받기를 멈춰 인터넷을 수업 화면에 양보합니다. 멈추거나 끝나면 이어서 받습니다")
        self.pause_streaming.toggled.connect(self.pause_streaming_toggled)
        hint = QLabel("유튜브 검색 결과를 오른쪽 클릭해 '받을 목록에 담기'를 누르면 여기에 쌓이고, 앱이 켜져 있는 동안 "
                      "하나씩 차례로 받습니다. 받은 영상은 라이브러리 '미리 받기'에 나옵니다.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        q_box = QWidget()
        ql = QVBoxLayout(q_box)
        ql.setContentsMargins(0, 6, 0, 0)
        ql.setSpacing(6)
        qh = QHBoxLayout()
        qh.addWidget(QLabel("<b>받을 목록</b>"))
        qh.addStretch(1)
        qh.addWidget(self.lbl_counts)
        ql.addLayout(qh)
        ql.addWidget(self.queue, 1)
        ql.addLayout(q_btns)
        ql.addWidget(self.pause_streaming)
        ql.addWidget(hint)

        split = QSplitter(Qt.Orientation.Vertical)
        split.setChildrenCollapsible(False)
        split.addWidget(src_box)
        split.addWidget(q_box)
        split.setSizes([170, 560])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 8)
        lay.setSpacing(6)
        lay.addLayout(head)
        lay.addWidget(self.lbl_status)
        lay.addWidget(split, 1)

        store.changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------
    def _on_toggle(self, on: bool) -> None:
        self._style_toggle(on)
        if on != self.store.enabled:
            self.enabled_toggled.emit(on)

    def _style_toggle(self, on: bool) -> None:
        self.btn_on.setText("● 켜짐" if on else "○ 꺼짐")
        self.btn_on.setToolTip("누르면 자동 받기를 멈춥니다" if on else "누르면 받을 목록을 차례로 받기 시작합니다")
        self.btn_on.setObjectName("primary" if on else "")
        self.btn_on.style().unpolish(self.btn_on)
        self.btn_on.style().polish(self.btn_on)

    def set_status(self, text: str) -> None:
        self.lbl_status.setText(text)

    def refresh(self) -> None:
        st = self.store
        self.btn_on.blockSignals(True)
        self.btn_on.setChecked(st.enabled)
        self.btn_on.blockSignals(False)
        self._style_toggle(st.enabled)

        self.sources.clear()
        for s in st.sources:
            if s.last_error:
                line2 = s.last_error
            elif s.playlist:
                line2 = f"{ago(s.last_check)} · 받은 영상은 '{s.playlist}' 재생목록에"
            else:
                line2 = ago(s.last_check)
            it = QListWidgetItem(f"{s.describe()}\n{line2}")
            it.setData(Qt.ItemDataRole.UserRole, s)
            if s.last_error:
                it.setForeground(QColor("#f87171"))
            it.setToolTip(s.url + (f"\n오류: {s.last_error}" if s.last_error else ""))
            self.sources.addItem(it)
        if not st.sources:
            ph = QListWidgetItem("아직 없음 — 아래 버튼으로 유튜브 재생목록이나 좋아요 목록을 추가하세요")
            ph.setFlags(Qt.ItemFlag.NoItemFlags)
            ph.setForeground(QColor("#6b6e7a"))
            self.sources.addItem(ph)

        sel = self._current_item()
        self.queue.clear()
        for q in st.items:
            label, color = STATUS.get(q.status, (q.status, "#8b8e99"))
            extra = q.error if q.status == "failed" else (Path(q.path).name if q.status == "done" else q.source)
            it = QListWidgetItem(f"{q.label()}\n{label}" + (f" · {extra}" if extra else ""))
            it.setData(Qt.ItemDataRole.UserRole, q)
            it.setIcon(QIcon(icons.pixmap({"done": "play", "failed": "minus", "running": "download"}.get(q.status, "download"),
                                          color, 14)))
            it.setToolTip(q.url + (f"\n{q.error}" if q.error else ""))
            if q.status == "running":
                it.setForeground(QColor("#fbbf24"))
            elif q.status == "failed":
                it.setForeground(QColor("#f87171"))
            self.queue.addItem(it)
            if q is sel:
                self.queue.setCurrentItem(it)
        c = st.counts()
        self.lbl_counts.setText(f"대기 {c['wait'] + c['running']} · 완료 {c['done']}" +
                                (f" · 실패 {c['failed']}" if c["failed"] else ""))
        self.lbl_counts.setObjectName("muted")

    def _current_item(self) -> QueueItem | None:
        it = self.queue.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _queue_activate(self, it: QListWidgetItem) -> None:
        q: QueueItem = it.data(Qt.ItemDataRole.UserRole)
        if q and q.status == "done" and q.path:
            self.play_requested.emit(q.path)

    def _queue_menu(self, pos) -> None:
        it = self.queue.itemAt(pos)
        if it is None:
            return
        q: QueueItem = it.data(Qt.ItemDataRole.UserRole)
        m = QMenu(self)
        acts = {}
        if q.status == "done":
            acts[m.addAction("재생")] = lambda: self.play_requested.emit(q.path)
            acts[m.addAction("폴더에서 보기")] = lambda: self.reveal_requested.emit(q.path)
        if q.status == "wait":
            acts[m.addAction("다음 차례로 받기")] = lambda: self.store.move_to_front(q)
        if q.status == "failed":
            acts[m.addAction("다시 받기")] = lambda: self.store.retry(q)
        acts[m.addAction("유튜브 링크 복사")] = lambda: QApplication.clipboard().setText(q.url)
        if q.status != "running":
            m.addSeparator()
            acts[m.addAction("목록에서 빼기")] = lambda: self.store.remove(q)
        a = m.exec(self.queue.viewport().mapToGlobal(pos))
        if a in acts:
            acts[a]()

    def _source_menu(self, pos) -> None:
        it = self.sources.itemAt(pos)
        if it is None:
            return
        s: Source | None = it.data(Qt.ItemDataRole.UserRole)
        if s is None:
            return
        m = QMenu(self)
        a_check = m.addAction("지금 확인")
        a_toggle = m.addAction("자동 확인 멈추기" if s.enabled else "자동 확인 다시 켜기")
        m.addSeparator()
        a_del = m.addAction("가져올 곳에서 빼기 (받은 파일은 그대로)")
        a = m.exec(self.sources.viewport().mapToGlobal(pos))
        if a is a_check:
            self.check_now_requested.emit()
        elif a is a_toggle:
            s.enabled = not s.enabled
            self.store.touch()
        elif a is a_del:
            self.store.remove_source(s)
