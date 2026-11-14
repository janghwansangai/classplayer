"""QSettings 기반 사용자 설정."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths


class Settings:
    def __init__(self) -> None:
        # 시험 스크립트는 CLASSPLAYER_TEST_SETTINGS=1 로 띄워 사용자 설정(다운로드 폴더 등)을 건드리지 않는다
        app = "ClassPlayerTest" if os.environ.get("CLASSPLAYER_TEST_SETTINGS") else "ClassPlayer"
        self.q = QSettings("ClassPlayer", app)

    # --- 라이브러리 ---
    @property
    def library_folders(self) -> list[str]:
        v = self.q.value("library/folders", [])
        if isinstance(v, str):
            v = [v]
        return [str(x) for x in (v or [])]

    @library_folders.setter
    def library_folders(self, folders: list[str]) -> None:
        self.q.setValue("library/folders", list(dict.fromkeys(folders)))

    # --- 다운로드 ---
    @property
    def download_dir(self) -> str:
        default = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation) or str(Path.home())
        return str(self.q.value("download/dir", str(Path(default) / "ClassPlayer")))

    @download_dir.setter
    def download_dir(self, d: str) -> None:
        self.q.setValue("download/dir", d)

    @property
    def download_preset(self) -> str:
        return str(self.q.value("download/preset", "mp4_1080"))

    @download_preset.setter
    def download_preset(self, p: str) -> None:
        self.q.setValue("download/preset", p)

    @property
    def download_subtitles(self) -> bool:
        return self.q.value("download/subtitles", True, type=bool)

    @download_subtitles.setter
    def download_subtitles(self, v: bool) -> None:
        self.q.setValue("download/subtitles", bool(v))

    @property
    def download_remove_sponsors(self) -> bool:
        return self.q.value("download/remove_sponsors", True, type=bool)

    @download_remove_sponsors.setter
    def download_remove_sponsors(self, v: bool) -> None:
        self.q.setValue("download/remove_sponsors", bool(v))

    # --- 스트리밍 ---
    @property
    def stream_max_height(self) -> int:
        return int(self.q.value("stream/max_height", 1080))

    @stream_max_height.setter
    def stream_max_height(self, h: int) -> None:
        self.q.setValue("stream/max_height", int(h))

    @property
    def sponsorblock_categories(self) -> list[str]:
        v = self.q.value("stream/sponsorblock", ["sponsor", "selfpromo", "interaction"])
        if isinstance(v, str):
            v = [v]
        return [str(x) for x in (v or [])]

    # --- 360도 ---
    @property
    def vr_max_magnification(self) -> float:
        return float(self.q.value("vr/max_magnification", 3.0))

    @vr_max_magnification.setter
    def vr_max_magnification(self, v: float) -> None:
        self.q.setValue("vr/max_magnification", float(v))

    @property
    def vr_hq(self) -> bool:
        return self.q.value("vr/hq", True, type=bool)

    @vr_hq.setter
    def vr_hq(self, v: bool) -> None:
        self.q.setValue("vr/hq", bool(v))

    # --- 캡처 / 소리 ---
    @property
    def capture_dir(self) -> str:
        default = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation) or str(Path.home())
        return str(self.q.value("capture/dir", str(Path(default) / "ClassPlayer 캡처")))

    @capture_dir.setter
    def capture_dir(self, d: str) -> None:
        self.q.setValue("capture/dir", d)

    @property
    def audio_device(self) -> str:
        return str(self.q.value("audio/device", "auto"))

    @audio_device.setter
    def audio_device(self, d: str) -> None:
        self.q.setValue("audio/device", d)

    # --- 일반 ---
    @property
    def volume(self) -> int:
        return int(self.q.value("player/volume", 100))

    @volume.setter
    def volume(self, v: int) -> None:
        self.q.setValue("player/volume", int(v))

    @property
    def recent(self) -> list[str]:
        v = self.q.value("player/recent", [])
        if isinstance(v, str):
            v = [v]
        return [str(x) for x in (v or [])]

    def add_recent(self, item: str, limit: int = 30) -> None:
        items = [item] + [r for r in self.recent if r != item]
        self.q.setValue("player/recent", items[:limit])
