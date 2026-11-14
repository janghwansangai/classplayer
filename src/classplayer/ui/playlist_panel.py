"""재생목록 창: 목록 고르기·만들기, 항목 추가·순서 바꾸기·빼기, 반복·섞기."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog,
                               QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox, QToolButton, QVBoxLayout,
                               QWidget)

from classplayer.bootstrap import MEDIA_EXTENSIONS
from classplayer.library.playlist import PlaylistStore
from classplayer.ui import icons


class _DropList(QListWidget):
    """파일이나 링크를 끌어다 놓으면 추가하는 목록."""

    dropped = Signal(list)   # [(target, title)]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:  # noqa: N802
        e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        md = e.mimeData()
        out = []
        if md.hasUrls():
            for u in md.urls():
                out.append((u.toLocalFile() if u.isLocalFile() else u.toString(), ""))
        elif md.hasText():
            out += [(t.strip(), "") for t in md.text().splitlines() if t.strip()]
        if out:
            self.dropped.emit(out)
        e.acceptProposedAction()


class PlaylistPanel(QWidget):
    play_requested = Signal(str, int)            # 목록 이름, 위치
    add_current_requested = Signal(str)          # 목록 이름

    def __init__(self, store: PlaylistStore, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("library")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.store = store

        self.combo = QComboBox()
        self.combo.setToolTip("재생목록 고르기")
        self.combo.currentIndexChanged.connect(lambda _i: self.refresh_items())

        def tbtn(name: str, tip: str, slot) -> QToolButton:
            b = QToolButton()
            b.setIcon(QIcon(icons.pixmap(name, "#d9dbe3", 16)))
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        b_new = tbtn("plus", "새 재생목록", self._new_list)
        b_more = QToolButton()
        b_more.setText("⋯")
        b_more.setToolTip("이름 바꾸기 · 삭제")
        b_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(b_more)
        m.addAction("이름 바꾸기…", self._rename_list)
        m.addAction("이 재생목록 삭제…", self._delete_list)
        b_more.setMenu(m)
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self.combo, 1)
        top.addWidget(b_new)
        top.addWidget(b_more)

        self.list = _DropList()
        self.list.setIconSize(QSize(18, 18))
        self.list.setSpacing(1)
        self.list.itemActivated.connect(self._play_item)
        self.list.itemDoubleClicked.connect(self._play_item)
        self.list.dropped.connect(self._add_many)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._item_menu)

        b_cur = tbtn("plus", "지금 보는 영상을 이 목록에 추가", lambda: self.add_current_requested.emit(self.current_name()))
        b_file = tbtn("folder", "파일 추가…", self._add_files)
        b_link = tbtn("link", "유튜브 링크 추가…", self._add_link)
        b_up = QToolButton()
        b_up.setText("▲")
        b_up.setToolTip("위로")
        b_up.clicked.connect(lambda: self._move(-1))
        b_down = QToolButton()
        b_down.setText("▼")
        b_down.setToolTip("아래로")
        b_down.clicked.connect(lambda: self._move(1))
        b_del = tbtn("minus", "목록에서 빼기", self._remove)
        tools = QHBoxLayout()
        tools.setSpacing(6)
        for b in (b_cur, b_file, b_link):
            tools.addWidget(b)
        tools.addStretch(1)
        for b in (b_up, b_down, b_del):
            tools.addWidget(b)

        self.repeat = QCheckBox("목록 반복")
        self.shuffle = QCheckBox("섞어서 재생")
        self.repeat.setChecked(store.repeat)
        self.shuffle.setChecked(store.shuffle)
        self.repeat.toggled.connect(lambda on: store.set_options(repeat=on))
        self.shuffle.toggled.connect(lambda on: store.set_options(shuffle=on))
        opts = QHBoxLayout()
        opts.addWidget(self.repeat)
        opts.addWidget(self.shuffle)
        opts.addStretch(1)

        self.hint = QLabel("영상 파일이나 유튜브 링크를 여기로 끌어다 놓거나, + 로 추가하세요. "
                           "두 번 누르면 그 영상부터 차례로 재생합니다.")
        self.hint.setWordWrap(True)
        self.hint.setObjectName("muted")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 8)
        lay.setSpacing(8)
        lay.addLayout(top)
        lay.addLayout(tools)
        lay.addWidget(self.list, 1)
        lay.addLayout(opts)
        lay.addWidget(self.hint)

        store.changed.connect(self.refresh)
        store.position_changed.connect(lambda _n, _i: self.refresh_items())
        self.refresh()

    # ------------------------------------------------------------------
    def current_name(self) -> str:
        return self.combo.currentText()

    def select_list(self, name: str) -> None:
        i = self.combo.findText(name)
        if i >= 0:
            self.combo.setCurrentIndex(i)

    def refresh(self) -> None:
        cur = self.current_name()
        self.combo.blockSignals(True)
        self.combo.clear()
        for n in self.store.names():
            p = self.store.get(n)
            self.combo.addItem(n)
            self.combo.setItemData(self.combo.count() - 1, f"{len(p.items) if p else 0}개", Qt.ItemDataRole.ToolTipRole)
        i = self.combo.findText(cur)
        self.combo.setCurrentIndex(i if i >= 0 else 0)
        self.combo.blockSignals(False)
        self.refresh_items()

    def refresh_items(self) -> None:
        name = self.current_name()
        p = self.store.get(name)
        row = self.list.currentRow()
        self.list.clear()
        if not p:
            return
        for i, it in enumerate(p.items):
            playing = self.store.active == name and self.store.index == i
            w = QListWidgetItem(f"{i + 1}.  " + it.label())
            w.setToolTip(it.target)
            w.setIcon(QIcon(icons.pixmap("link" if it.is_link else "play", "#fbbf24" if playing else "#8b8e99", 16)))
            if playing:
                f = w.font()
                f.setBold(True)
                w.setFont(f)
            self.list.addItem(w)
        if 0 <= row < self.list.count():
            self.list.setCurrentRow(row)
        self.hint.setVisible(not p.items)

    # ------------------------------------------------------------------ 동작
    def _new_list(self) -> None:
        name, ok = QInputDialog.getText(self, "새 재생목록", "목록 이름", text=self.store.unique_name("새 목록"))
        if ok:
            self.select_list(self.store.create(name))

    def _rename_list(self) -> None:
        old = self.current_name()
        name, ok = QInputDialog.getText(self, "이름 바꾸기", "새 이름", text=old)
        if ok:
            self.select_list(self.store.rename(old, name))

    def _delete_list(self) -> None:
        name = self.current_name()
        r = QMessageBox.question(self, "재생목록 삭제", f"'{name}' 목록을 지울까요? (영상 파일은 지워지지 않습니다)")
        if r == QMessageBox.StandardButton.Yes:
            self.store.delete(name)

    def add_target(self, target: str, title: str = "", name: str | None = None) -> bool:
        return self.store.add(name or self.current_name(), target, title)

    def _add_many(self, items: list) -> None:
        n = 0
        for target, title in items:
            if target.startswith(("http://", "https://")) or Path(target).suffix.lower() in MEDIA_EXTENSIONS:
                n += 1 if self.add_target(target, title) else 0
        if not n:
            QMessageBox.information(self, "재생목록", "영상·소리 파일이나 링크만 넣을 수 있습니다.")

    def _add_files(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(MEDIA_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(self, "재생목록에 추가할 파일", "", f"영상·소리 ({exts})")
        self._add_many([(p, "") for p in paths])

    def _add_link(self) -> None:
        url, ok = QInputDialog.getText(self, "링크 추가", "유튜브 등 영상 링크")
        if ok and url.strip():
            self._add_many([(url.strip(), "")])

    def _move(self, delta: int) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        j = self.store.move(self.current_name(), row, delta)
        self.list.setCurrentRow(j)

    def _remove(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.store.remove(self.current_name(), row)

    def _play_item(self, item) -> None:
        self.play_requested.emit(self.current_name(), self.list.row(item))

    def _item_menu(self, pos) -> None:
        it = self.list.itemAt(pos)
        if it is None:
            return
        row = self.list.row(it)
        m = QMenu(self)
        a_play = m.addAction("여기부터 재생")
        a_up = m.addAction("위로")
        a_down = m.addAction("아래로")
        a_del = m.addAction("목록에서 빼기")
        a = m.exec(self.list.viewport().mapToGlobal(pos))
        if a is a_play:
            self.play_requested.emit(self.current_name(), row)
        elif a in (a_up, a_down):
            self.list.setCurrentRow(row)
            self._move(-1 if a is a_up else 1)
        elif a is a_del:
            self.store.remove(self.current_name(), row)
