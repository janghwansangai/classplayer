"""영상 라이브러리: 지정한 폴더를 훑어 목록을 만들고 검색한다."""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QToolButton, QVBoxLayout, QWidget)

from classplayer.bootstrap import VIDEO_EXTENSIONS


@dataclass
class Entry:
    path: str
    name: str
    folder: str
    size: int
    mtime: float


def scan_folders(folders: list[str], max_files: int = 20000) -> list[Entry]:
    seen: set[str] = set()
    out: list[Entry] = []
    for root in folders:
        rp = Path(root)
        if not rp.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(rp):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if Path(fn).suffix.lower() not in VIDEO_EXTENSIONS or fn.startswith("."):
                    continue
                full = os.path.join(dirpath, fn)
                if full in seen:
                    continue
                seen.add(full)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                out.append(Entry(full, fn, os.path.basename(dirpath), st.st_size, st.st_mtime))
                if len(out) >= max_files:
                    return out
    out.sort(key=lambda e: e.mtime, reverse=True)
    return out


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


class _ScanNotifier(QObject):
    done = Signal(object)


class LibraryPanel(QWidget):
    play_requested = Signal(str)
    folders_changed = Signal(list)

    def __init__(self, folders: list[str], extra_folders: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._folders = list(folders)
        self._extra = list(extra_folders or [])   # 다운로드 폴더처럼 항상 포함되는 폴더
        self._entries: list[Entry] = []
        self._notifier = _ScanNotifier()
        self._notifier.done.connect(self._on_scanned)

        self.search = QLineEdit()
        self.search.setPlaceholderText("영상 검색 (파일명, 폴더명)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.itemActivated.connect(self._on_activated)
        self.list.itemDoubleClicked.connect(self._on_activated)

        self.folder_combo = QComboBox()
        self.folder_combo.setToolTip("라이브러리 폴더")
        self.btn_add = QToolButton()
        self.btn_add.setText("+")
        self.btn_add.setToolTip("폴더 추가")
        self.btn_add.clicked.connect(self.add_folder_dialog)
        self.btn_remove = QToolButton()
        self.btn_remove.setText("−")
        self.btn_remove.setToolTip("선택한 폴더 제거")
        self.btn_remove.clicked.connect(self._remove_current_folder)
        self.btn_refresh = QToolButton()
        self.btn_refresh.setText("↻")
        self.btn_refresh.setToolTip("다시 훑기")
        self.btn_refresh.clicked.connect(self.rescan)
        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet("color: gray;")

        top = QHBoxLayout()
        top.addWidget(self.folder_combo, 1)
        top.addWidget(self.btn_add)
        top.addWidget(self.btn_remove)
        top.addWidget(self.btn_refresh)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addLayout(top)
        lay.addWidget(self.search)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.lbl_count)
        self._refresh_combo()
        self.rescan()

    # ------------------------------------------------------------------
    @property
    def folders(self) -> list[str]:
        return list(self._folders)

    def set_extra_folders(self, extra: list[str]) -> None:
        self._extra = list(extra)
        self.rescan()

    def _refresh_combo(self) -> None:
        self.folder_combo.clear()
        for f in self._folders:
            self.folder_combo.addItem(Path(f).name or f, f)
        if not self._folders:
            self.folder_combo.addItem("폴더를 추가하세요 (+)", "")

    def add_folder_dialog(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "라이브러리에 추가할 폴더")
        if d:
            self.add_folder(d)

    def add_folder(self, folder: str) -> None:
        if folder and folder not in self._folders:
            self._folders.append(folder)
            self._refresh_combo()
            self.folders_changed.emit(self.folders)
            self.rescan()

    def _remove_current_folder(self) -> None:
        f = self.folder_combo.currentData()
        if f in self._folders:
            self._folders.remove(f)
            self._refresh_combo()
            self.folders_changed.emit(self.folders)
            self.rescan()

    def rescan(self) -> None:
        folders = list(dict.fromkeys(self._folders + self._extra))
        self.lbl_count.setText("훑는 중...")

        def work() -> None:
            self._notifier.done.emit(scan_folders(folders))

        threading.Thread(target=work, daemon=True).start()

    def _on_scanned(self, entries: object) -> None:
        self._entries = list(entries)  # type: ignore[arg-type]
        self._apply_filter(self.search.text())

    def _apply_filter(self, text: str) -> None:
        tokens = [t for t in text.lower().split() if t]
        self.list.clear()
        shown = 0
        for e in self._entries:
            hay = (e.name + " " + e.folder).lower()
            if all(t in hay for t in tokens):
                item = QListWidgetItem(f"{e.name}\n{e.folder} · {_human_size(e.size)}")
                item.setToolTip(e.path)
                item.setData(Qt.ItemDataRole.UserRole, e.path)
                self.list.addItem(item)
                shown += 1
        self.lbl_count.setText(f"{shown}개 표시 / 전체 {len(self._entries)}개")

    def _on_activated(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.play_requested.emit(str(path))

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()
