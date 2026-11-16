"""자동 받기: '받을 목록'에 담아 둔 유튜브 영상을 앱이 켜져 있는 동안 하나씩 차례로 받는다.

- 받을 목록(대기열): 검색 결과·링크·재생목록 링크 영상을 담는다. 앱을 다시 켜도 남는다(autodownload.json).
- 가져올 곳(출처): 유튜브 재생목록 링크(로그인 불필요) 또는 '좋아요 표시한 동영상'·'나중에 볼 동영상'
  (고른 브라우저의 유튜브 로그인 정보를 그 순간에만 읽음, 저장하지 않음). 주기적으로 확인해 새 영상만 담는다.
- 받기는 기존 미리 받기(DownloadJob)를 그대로 쓴다. 실행기(AutoDownloader)가 작업 하나가 끝나면 다음을 시작한다.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QStandardPaths, QTimer, Signal

from classplayer.youtube.common import youtube_video_id

LIKED_URL = "https://www.youtube.com/playlist?list=LL"
WATCH_LATER_URL = "https://www.youtube.com/playlist?list=WL"
MAX_ATTEMPTS = 3

# 브라우저 이름(yt-dlp) → 화면 이름. 윈도우의 최신 크롬·엣지는 앱 전용 암호화로 다른 프로그램이 못 읽는다.
BROWSERS_MAC = [("chrome", "크롬"), ("safari", "사파리"), ("edge", "엣지"), ("firefox", "파이어폭스"),
                ("whale", "웨일"), ("brave", "브레이브")]
BROWSERS_WIN = [("firefox", "파이어폭스 (추천)"), ("whale", "웨일"), ("chrome", "크롬"), ("edge", "엣지"),
                ("brave", "브레이브")]


def browser_choices() -> list[tuple[str, str]]:
    return BROWSERS_MAC if sys.platform == "darwin" else BROWSERS_WIN


def browser_label(key: str) -> str:
    return dict(BROWSERS_MAC + BROWSERS_WIN).get(key, key).replace(" (추천)", "")


@dataclass
class QueueItem:
    url: str
    title: str = ""
    vid: str = ""                 # 유튜브 영상 ID (같은 영상 두 번 담지 않기)
    status: str = "wait"          # wait | running | done | failed
    path: str = ""                # 받은 파일
    error: str = ""
    source: str = ""              # 담은 곳 (검색, 링크, 출처 이름)
    playlist: str = ""            # 받은 뒤 넣을 ClassPlayer 재생목록 (비면 넣지 않음)
    replace_link: bool = False    # 재생목록 안의 같은 링크를 받은 파일로 바꾸기
    added: float = 0.0
    attempts: int = 0

    def label(self) -> str:
        return self.title or self.url


@dataclass
class Source:
    kind: str                     # playlist | liked | watchlater
    url: str
    name: str
    browser: str = ""             # liked/watchlater 일 때 로그인 정보를 읽을 브라우저
    playlist: str = ""            # 받은 영상을 넣을 ClassPlayer 재생목록
    seen: list[str] = field(default_factory=list)   # 이미 확인한 영상 ID
    last_check: float = 0.0
    last_error: str = ""
    enabled: bool = True

    def describe(self) -> str:
        if self.kind == "playlist":
            return f"유튜브 재생목록 · {self.name}"
        what = "좋아요 표시한 동영상" if self.kind == "liked" else "나중에 볼 동영상"
        return f"{what} ({browser_label(self.browser)})"


class AutoDownloadStore(QObject):
    """받을 목록과 가져올 곳을 들고 있고 파일로 저장한다."""

    changed = Signal()

    def __init__(self, path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        if path is None:
            base = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation) or ".")
            base.mkdir(parents=True, exist_ok=True)
            path = base / "autodownload.json"
        self.path = path
        self.items: list[QueueItem] = []
        self.sources: list[Source] = []
        self.enabled = True
        self.load()

    # ------------------------------------------------------------------ 저장
    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
        known_q = set(QueueItem.__dataclass_fields__)
        known_s = set(Source.__dataclass_fields__)
        self.items = [QueueItem(**{k: v for k, v in d.items() if k in known_q}) for d in data.get("items", [])]
        self.sources = [Source(**{k: v for k, v in d.items() if k in known_s}) for d in data.get("sources", [])]
        self.enabled = bool(data.get("enabled", True))
        for it in self.items:            # 앱이 받는 도중에 꺼졌으면 다시 대기로
            if it.status == "running":
                it.status = "wait"

    def save(self) -> None:
        data = {"enabled": self.enabled, "items": [asdict(i) for i in self.items],
                "sources": [asdict(s) for s in self.sources]}
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def touch(self) -> None:
        self.save()
        self.changed.emit()

    # ------------------------------------------------------------------ 받을 목록
    def find(self, url: str) -> QueueItem | None:
        vid = youtube_video_id(url)
        return next((i for i in self.items if (vid and i.vid == vid) or i.url == url), None)

    def add(self, url: str, title: str = "", source: str = "", playlist: str = "",
            replace_link: bool = False, save: bool = True) -> bool:
        """담기. 이미 있으면(실패한 것 제외) False. 실패했던 것이면 다시 대기로 돌린다."""
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            return False
        old = self.find(url)
        if old is not None:
            if old.status == "failed":
                old.status, old.error, old.attempts = "wait", "", 0
                if save:
                    self.touch()
                return True
            return False
        self.items.append(QueueItem(url=url, title=title, vid=youtube_video_id(url) or "", source=source,
                                    playlist=playlist, replace_link=replace_link, added=time.time()))
        if save:
            self.touch()
        return True

    def add_many(self, entries: list[tuple[str, str]], source: str = "", playlist: str = "",
                 replace_link: bool = False) -> int:
        n = sum(1 for url, title in entries if self.add(url, title, source, playlist, replace_link, save=False))
        if n:
            self.touch()
        return n

    def next_waiting(self) -> QueueItem | None:
        return next((i for i in self.items if i.status == "wait"), None)

    def counts(self) -> dict[str, int]:
        c = {"wait": 0, "running": 0, "done": 0, "failed": 0}
        for i in self.items:
            c[i.status] = c.get(i.status, 0) + 1
        return c

    def remove(self, item: QueueItem) -> None:
        if item in self.items and item.status != "running":
            self.items.remove(item)
            self.touch()

    def move_to_front(self, item: QueueItem) -> None:
        if item in self.items and item.status == "wait":
            self.items.remove(item)
            first = next((k for k, i in enumerate(self.items) if i.status == "wait"), len(self.items))
            self.items.insert(first, item)
            self.touch()

    def retry(self, item: QueueItem | None = None) -> int:
        targets = [item] if item else [i for i in self.items if i.status == "failed"]
        n = 0
        for i in targets:
            if i and i.status == "failed":
                i.status, i.error, i.attempts = "wait", "", 0
                n += 1
        if n:
            self.touch()
        return n

    def clear_done(self) -> int:
        before = len(self.items)
        self.items = [i for i in self.items if i.status != "done"]
        if len(self.items) != before:
            self.touch()
        return before - len(self.items)

    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        self.touch()

    # ------------------------------------------------------------------ 가져올 곳
    def add_source(self, src: Source) -> Source:
        for s in self.sources:
            if s.kind == src.kind and s.url == src.url and s.browser == src.browser:
                return s
        self.sources.append(src)
        self.touch()
        return src

    def remove_source(self, src: Source) -> None:
        if src in self.sources:
            self.sources.remove(src)
            self.touch()


# ---------------------------------------------------------------------- 출처에서 목록 가져오기
def ytdlp_list_opts(browser: str = "", js_runtime: str | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "skip_download": True,
                            "extract_flat": "in_playlist", "socket_timeout": 20, "noprogress": True,
                            "lazy_playlist": False}
    if browser:
        opts["cookiesfrombrowser"] = (browser, None, None, None)
    if js_runtime:
        opts["js_runtimes"] = {js_runtime: {"path": None}}
    opts["logger"] = _Quiet()      # 오류는 예외로 받아 알기 쉬운 말로 보여 주므로 콘솔에는 찍지 않는다
    return opts


class _Quiet:
    def debug(self, msg: str) -> None:
        pass

    warning = error = info = debug


def explain_error(e: BaseException, browser: str = "") -> str:
    """yt-dlp 오류를 선생님이 알아볼 수 있는 말로."""
    msg = str(e)
    low = msg.lower()
    name = browser_label(browser) if browser else "브라우저"
    if browser:
        if "could not find" in low and "cookies" in low:
            return f"{name}에서 로그인 기록을 찾지 못했습니다. {name}을(를) 설치했고 유튜브에 로그인했는지 확인하세요"
        if browser == "safari" and ("operation not permitted" in low or "permission" in low):
            return ("사파리 로그인 정보를 읽을 권한이 없습니다. 시스템 설정 › 개인정보 보호 및 보안 › 전체 디스크 접근 권한에서 "
                    "ClassPlayer 를 허용하거나 다른 브라우저를 고르세요")
        if "dpapi" in low or "decrypt" in low or "app-bound" in low or "v20" in low:
            return (f"{name}의 로그인 정보는 보안 정책 때문에 다른 프로그램이 읽을 수 없습니다. "
                    "파이어폭스로 유튜브에 로그인해 쓰거나, '재생목록 링크' 방식을 쓰세요")
        if "keyring" in low or "keychain" in low or "safe storage" in low:
            return f"키체인에서 {name} 로그인 정보 사용을 허용하지 않았습니다. 다시 시도해 '허용'을 누르세요"
        if "database is locked" in low:
            return f"{name}이(가) 로그인 정보를 쓰고 있어 읽지 못했습니다. {name}을(를) 닫고 다시 시도하세요"
    if "playlist does not exist" in low or "this playlist is private" in low or "sign in" in low or "login" in low:
        return ("목록을 볼 수 없습니다. 좋아요·나중에 볼 동영상은 유튜브에 로그인한 브라우저가 필요하고, "
                "재생목록은 '공개' 또는 '일부 공개'여야 합니다")
    if "unable to download" in low or "timed out" in low or "urlopen" in low:
        return "인터넷 연결이 불안정해 목록을 가져오지 못했습니다"
    return msg.splitlines()[0][:200] if msg else type(e).__name__


def fetch_source(src: Source, js_runtime: str | None = None) -> tuple[str, list[tuple[str, str, str]]]:
    """출처의 영상 목록. 돌려주는 값: (목록 제목, [(영상 ID, 링크, 제목)]). 실패하면 예외."""
    import yt_dlp
    with yt_dlp.YoutubeDL(ytdlp_list_opts(src.browser, js_runtime)) as ydl:
        info = ydl.extract_info(src.url, download=False)
    out: list[tuple[str, str, str]] = []
    for e in (info or {}).get("entries") or []:
        if not e:
            continue
        vid = str(e.get("id") or "")
        if not vid or len(vid) != 11:
            continue
        title = str(e.get("title") or "")
        if title in ("[Private video]", "[Deleted video]"):
            continue
        out.append((vid, f"https://www.youtube.com/watch?v={vid}", title))
    return str((info or {}).get("title") or ""), out


def is_playlist_url(url: str) -> bool:
    from urllib.parse import parse_qs, urlparse
    try:
        u = urlparse(url.strip())
    except ValueError:
        return False
    return "youtube.com" in (u.netloc or "") and bool(parse_qs(u.query).get("list"))


def playlist_url(url: str) -> str:
    """watch?v=…&list=… 링크도 재생목록 주소로 바꾼다."""
    from urllib.parse import parse_qs, urlparse
    lid = parse_qs(urlparse(url.strip()).query).get("list", [""])[0]
    return f"https://www.youtube.com/playlist?list={lid}" if lid else url


# ---------------------------------------------------------------------- 실행기
class _Notifier(QObject):
    fetched = Signal(object, str, object, str)      # Source, 목록 제목, [(vid,url,title)], 오류


class AutoDownloader(QObject):
    """받을 목록을 앞에서부터 하나씩 받는다. 출처는 주기적으로 확인해 새 영상만 담는다."""

    status_changed = Signal(str)
    item_finished = Signal(object)            # QueueItem (done 이면 path 가 있음)
    source_fetched = Signal(object, str, object, str)   # Source, 목록 제목, 항목, 오류 (첫 추가 때 등)

    def __init__(self, store: AutoDownloadStore, make_job: Callable[[QueueItem], Any],
                 wait_reason: Callable[[], str], js_runtime: str | None = None,
                 check_minutes: int = 60, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.make_job = make_job
        self.wait_reason = wait_reason
        self.js_runtime = js_runtime
        self.check_minutes = check_minutes
        self.job = None
        self.current: QueueItem | None = None
        self.status = ""
        self._closing = False
        self._checking: set[int] = set()
        self._notifier = _Notifier()
        self._notifier.fetched.connect(self._on_fetched)
        self._pending_first: dict[int, bool] = {}   # 처음 추가한 출처 (자동으로 담지 않고 물어봄)
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._tick)
        self._src_timer = QTimer(self)
        self._src_timer.setInterval(5 * 60 * 1000)
        self._src_timer.timeout.connect(lambda: self.check_sources(force=False))

    def start(self) -> None:
        self._timer.start()
        self._src_timer.start()
        QTimer.singleShot(20_000, lambda: self.check_sources(force=False))

    def busy(self) -> bool:
        return self.job is not None and self.job.is_running()

    def _set_status(self, s: str) -> None:
        if s != self.status:
            self.status = s
            self.status_changed.emit(s)

    # ------------------------------------------------------------------ 받기
    def _tick(self) -> None:
        if self.busy():
            return
        st = self.store
        if not st.enabled:
            self._set_status("꺼짐 — 켜면 받을 목록을 차례로 받습니다")
            return
        nxt = st.next_waiting()
        if nxt is None:
            c = st.counts()
            self._set_status(f"모두 받았습니다 (완료 {c['done']}개" + (f", 실패 {c['failed']}개)" if c["failed"] else ")"))
            return
        why = self.wait_reason()
        if why:
            self._set_status(f"쉬는 중 — {why}")
            return
        self._start(nxt)

    def run_now(self) -> None:
        QTimer.singleShot(0, self._tick)

    def _start(self, item: QueueItem) -> None:
        item.status, item.error = "running", ""
        item.attempts += 1
        self.current = item
        self.store.touch()
        try:
            job = self.make_job(item)
        except Exception as e:  # noqa: BLE001
            self._done(item, "", str(e))
            return
        self.job = job
        # 작업 스레드에서 오는 신호: 이 QObject 의 메서드에 이어야 메인 스레드로 넘어온다 (lambda 는 그 스레드에서 실행됨)
        job.failed.connect(self._on_job_failed)
        job.finished.connect(self._on_job_finished)
        self._set_status(f"받는 중 — {item.label()}")

    def _on_job_failed(self, msg: str) -> None:
        if self.current is not None:
            self.current.error = msg

    def _on_job_finished(self, path: str) -> None:
        item = self.current
        if item is not None:
            self._done(item, path, item.error)

    def _done(self, item: QueueItem, path: str, err: str) -> None:
        self.job = None
        self.current = None
        if path:
            item.status, item.path, item.error = "done", path, ""
        elif self._closing or (err == "취소됨" and not self.store.enabled):
            item.status, item.error = "wait", ""         # 앱을 끄거나 자동 받기를 껐을 때: 다음에 이어서
            item.attempts = max(0, item.attempts - 1)
        elif item.attempts < MAX_ATTEMPTS and err != "취소됨":
            item.status = "wait"                     # 다른 영상을 먼저 받고 나중에 다시
            self.store.items.remove(item)
            self.store.items.append(item)
        else:
            item.status, item.error = "failed", err or "받지 못했습니다"
        self.store.touch()
        self.item_finished.emit(item)
        QTimer.singleShot(1500, self._tick)

    def stop_current(self) -> None:
        if self.busy():
            self.job.cancel()

    def shutdown(self) -> None:
        """앱을 끌 때: 받던 영상은 다음에 이어 받도록 대기로 남긴다."""
        self._closing = True
        self._timer.stop()
        self._src_timer.stop()
        if self.current is not None:
            self.current.status = "wait"
            self.current.attempts = max(0, self.current.attempts - 1)
            self.store.save()

    # ------------------------------------------------------------------ 출처 확인
    def check_sources(self, force: bool = True) -> None:
        now = time.time()
        for src in list(self.store.sources):
            if not src.enabled:
                continue
            if not force and now - src.last_check < self.check_minutes * 60:
                continue
            self.fetch(src)

    def fetch(self, src: Source, first: bool = False) -> None:
        key = id(src)
        if key in self._checking:
            return
        self._checking.add(key)
        if first:
            self._pending_first[key] = True
        self._set_status(f"새 영상 확인 중 — {src.describe()}")
        js = self.js_runtime

        def work() -> None:
            try:
                title, entries = fetch_source(src, js)
                self._notifier.fetched.emit(src, title, entries, "")
            except Exception as e:  # noqa: BLE001
                self._notifier.fetched.emit(src, "", [], explain_error(e, src.browser))

        threading.Thread(target=work, daemon=True).start()

    def _on_fetched(self, src: Source, title: str, entries, err: str) -> None:
        key = id(src)
        self._checking.discard(key)
        first = self._pending_first.pop(key, False)
        src.last_check = time.time()
        src.last_error = err
        if not err and not first and src in self.store.sources:
            seen = set(src.seen)
            new = [(url, t) for vid, url, t in entries if vid not in seen]
            src.seen = list(dict.fromkeys(src.seen + [vid for vid, _u, _t in entries]))
            if new:
                # 좋아요는 최신이 위, 재생목록은 순서대로 → 받을 목록에는 화면 순서 그대로 담는다
                n = self.store.add_many(new, source=src.name, playlist=src.playlist)
                if n:
                    self._set_status(f"새 영상 {n}개를 받을 목록에 담았습니다 — {src.name}")
        self.store.touch()
        self.source_fetched.emit(src, title, entries, err)
        if not self.busy():
            QTimer.singleShot(500, self._tick)
