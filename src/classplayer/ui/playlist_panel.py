"""재생목록 창: 목록 고르기·만들기, 항목 추가·순서 바꾸기·빼기, 반복·섞기."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QGridLayout, QHBoxLayout, QInputDialog,
                               QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSplitter,
                               QToolButton, QVBoxLayout, QWidget)

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

        # ---- 위: 내 재생목록 (이름별로 만들고 관리)
        head1 = QLabel("<b>내 재생목록</b>")
        self.lists = QListWidget()
        self.lists.setObjectName("pllists")
        self.lists.setSpacing(1)
        self.lists.currentRowChanged.connect(lambda _r: self.refresh_items())
        self.lists.itemDoubleClicked.connect(lambda _it: self._play_all())
        self.lists.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.lists.customContextMenuRequested.connect(self._list_menu)

        def pbtn(text: str, tip: str, slot) -> QPushButton:
            b = QPushButton(text)
            b.setObjectName("chip")
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            return b

        mg = QGridLayout()
        mg.setHorizontalSpacing(6)
        mg.setVerticalSpacing(6)
        for k, (text, tip, slot) in enumerate((
                ("새 목록", "이름을 정해 새 재생목록 만들기", self._new_list),
                ("이름 바꾸기", "고른 재생목록 이름 바꾸기", self._rename_list),
                ("복제", "고른 재생목록을 복사해 새 목록 만들기", self._dup_list),
                ("삭제", "고른 재생목록 지우기 (영상 파일은 그대로)", self._delete_list),
                ("내보내기", "M3U 파일로 저장 (다른 컴퓨터·VLC 에서도 열림)", self._export_list),
                ("가져오기", "M3U/M3U8 재생목록 파일 불러오기", self._import_list))):
            mg.addWidget(pbtn(text, tip, slot), k // 3, k % 3)
        top_box = QWidget()
        tl = QVBoxLayout(top_box)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(6)
        tl.addWidget(head1)
        tl.addWidget(self.lists, 1)
        tl.addLayout(mg)

        def tbtn(name: str, tip: str, slot) -> QToolButton:
            b = QToolButton()
            b.setIcon(QIcon(icons.pixmap(name, "#d9dbe3", 16)))
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        # ---- 아래: 고른 목록의 영상
        self.title = QLabel("")
        self.title.setWordWrap(True)
        self.btn_play_all = QPushButton("▶  처음부터 재생")
        self.btn_play_all.setObjectName("primary")
        self.btn_play_all.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play_all.clicked.connect(self._play_all)
        head2 = QHBoxLayout()
        head2.addWidget(self.title, 1)
        head2.addWidget(self.btn_play_all)

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

        bottom_box = QWidget()
        bl = QVBoxLayout(bottom_box)
        bl.setContentsMargins(0, 6, 0, 0)
        bl.setSpacing(8)
        bl.addLayout(head2)
        bl.addLayout(tools)
        bl.addWidget(self.list, 1)
        bl.addLayout(opts)
        bl.addWidget(self.hint)
        split = QSplitter(Qt.Orientation.Vertical)
        split.setChildrenCollapsible(False)
        split.addWidget(top_box)
        split.addWidget(bottom_box)
        split.setSizes([210, 520])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 8)
        lay.addWidget(split, 1)

        store.changed.connect(self.refresh)
        store.position_changed.connect(lambda _n, _i: self.refresh())
        self.refresh()

    # ------------------------------------------------------------------
    def current_name(self) -> str:
        it = self.lists.currentItem()
        return str(it.data(Qt.ItemDataRole.UserRole)) if it else ""

    def select_list(self, name: str) -> None:
        for r in range(self.lists.count()):
            if self.lists.item(r).data(Qt.ItemDataRole.UserRole) == name:
                self.lists.setCurrentRow(r)
                return

    def refresh(self) -> None:
        cur = self.current_name()
        self.lists.blockSignals(True)
        self.lists.clear()
        for n in self.store.names():
            p = self.store.get(n)
            playing = self.store.active == n
            w = QListWidgetItem(f"{'▶ ' if playing else ''}{n}    {len(p.items) if p else 0}개")
            w.setData(Qt.ItemDataRole.UserRole, n)
            w.setToolTip("두 번 누르면 처음부터 재생")
            self.lists.addItem(w)
        names = self.store.names()
        self.lists.setCurrentRow(names.index(cur) if cur in names else 0)
        self.lists.blockSignals(False)
        self.refresh_items()

    def refresh_items(self) -> None:
        name = self.current_name()
        p = self.store.get(name)
        row = self.list.currentRow()
        self.list.clear()
        if not p:
            return
        self.title.setText(f"<b>{name}</b> · 영상 {len(p.items)}개")
        self.btn_play_all.setEnabled(bool(p.items))
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

    def _play_all(self) -> None:
        if self.current_name():
            self.play_requested.emit(self.current_name(), 0)

    def _dup_list(self) -> None:
        name = self.current_name()
        if name:
            self.select_list(self.store.duplicate(name))

    def _export_list(self) -> None:
        name = self.current_name()
        p = self.store.get(name)
        if not p or not p.items:
            QMessageBox.information(self, "내보내기", "영상이 들어 있는 재생목록을 골라 주세요.")
            return
        safe = "".join(c for c in name if c not in '\\/:*?"<>|').strip() or "재생목록"
        path, _ = QFileDialog.getSaveFileName(self, "재생목록 내보내기", f"{safe}.m3u8", "재생목록 (*.m3u8 *.m3u)")
        if path:
            n = self.store.export_m3u(name, path)
            QMessageBox.information(self, "내보내기", f"영상 {n}개를 저장했습니다.\n{path}\n\n"
                                    "다른 컴퓨터에서는 '가져오기'로 열 수 있습니다. 파일 영상은 같은 위치에 있어야 재생됩니다.")

    def _import_list(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "재생목록 가져오기", "", "재생목록 (*.m3u8 *.m3u)")
        if not path:
            return
        try:
            name, n = self.store.import_m3u(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "가져오기", f"읽지 못했습니다: {e}")
            return
        self.select_list(name)
        QMessageBox.information(self, "가져오기", f"'{name}' 재생목록으로 영상 {n}개를 가져왔습니다.")

    def _list_menu(self, pos) -> None:
        it = self.lists.itemAt(pos)
        if it is None:
            return
        self.lists.setCurrentItem(it)
        m = QMenu(self)
        acts = [(m.addAction("처음부터 재생"), self._play_all), (m.addAction("이름 바꾸기…"), self._rename_list),
                (m.addAction("복제"), self._dup_list), (m.addAction("내보내기…"), self._export_list),
                (m.addAction("삭제…"), self._delete_list)]
        a = m.exec(self.lists.viewport().mapToGlobal(pos))
        for act, fn in acts:
            if a is act:
                fn()

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
