"""드라이브(구글 드라이브 등 클라우드) 파일 빠르고 안정적인 재생.

앱이 그 파일의 '유일한 읽기 창구'가 되어 4MB 조각으로 앞서 받아 두고, mpv 에는 받아 둔 조각을 넘긴다
(mpv 사용자 정의 스트림 cpcloud://). mpv·ffprobe·ffmpeg 가 같은 드라이브 파일을 동시에 읽어 다투지 않고,
드라이브가 '시간 초과'를 돌려줘도 다시 읽어서 재생이 끊기거나 끝나 버리지 않는다.

2026-11-19 측정 (맥, Google Drive File Provider, 아직 안 받은 '스트리밍' 파일 = SF_DATALESS, 집 인터넷):
 - 부분 읽기가 된다 (파일 전체를 받지 않음). 한 갈래로 순서대로 4MB 씩: 조각마다 0.6~2.7초, 평균 3MB/s(약 24Mbps).
 - 여러 갈래로 동시에 읽어도 드라이브가 차례로 처리해 빨라지지 않고, ETIMEDOUT(errno 60)이 나거나
   드라이브가 '통째로 받기'로 바꿔 다 받을 때까지(352MB 17초) 모든 읽기를 붙잡기도 했다 → 앞서 받기는 한 갈래만.
 - 통째로 받기(빠른 길)는 APFS 복제(clonefile, cp -c)를 요청하면 일어난다: 413MB 33초(12MB/s), 453MB 22초(20MB/s).
   /dev/null 로 그냥 복사하면 느린 길(453MB 155초). 다른 읽기가 진행 중이면 복제도 느린 길로 간다 → 읽기를 멈추고 복제.
 그래서 앱은: 가벼운 영상은 한 갈래 부분 읽기로 바로 재생, 드라이브 속도보다 무거운 영상은 첫 화면 뒤
 잠깐 멈추고 빠른 길로 통째로 받은 다음 이어서 재생 (app.MainWindow._cloud_* 참고).
윈도우(구글 드라이브 G: 가상 드라이브)는 복제가 없어, 끝까지 받기는 재생을 이어 가며 뒤에서 차례로 읽는다 (start_full).
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

PROTO = "cpcloud"
CHUNK = 4 << 20          # 조각 크기 (드라이브 요청 한 번에 0.6~2.7초)
AHEAD = 16               # 읽는 자리 앞으로 받아 둘 조각 수 (64MB, mpv 자체 읽기 버퍼와 별도)
BEHIND = 4               # 뒤로 감기 대비 남겨 둘 조각 수
WORKERS = 2              # 한 갈래는 앞서 받기, 한 갈래는 탐색 직후 '지금 필요한 조각' 몫 (동시 읽기 최대 2)
RETRIES = 8              # 조각 하나를 다시 읽는 횟수
RETRY_DELAY = 0.3        # 첫 재시도까지 기다림 (두 배씩 늘려 최대 4초)
READ_TIMEOUT = 120.0     # mpv 가 조각 하나를 이만큼 기다려도 안 오면 오류 (통째로 받는 중엔 기다림)
PARTIAL_RATE_DEFAULT = 3.0e6   # 부분 읽기 속도 기본값 (바이트/초) — 실제 값은 rate() 로 잰다
_DEBUG = bool(os.environ.get("CLASSPLAYER_CLOUD_DEBUG"))
_T0 = time.monotonic()


def _log(msg: str) -> None:
    if _DEBUG:
        print(f"[cloud {time.monotonic() - _T0:7.2f}] {msg}", flush=True)


class CloudReader:
    """조각 단위로 앞서 받아 두는 읽기 장치. mpv 스트림 콜백(read/seek/size/close/cancel)이 부른다."""

    def __init__(self, path: str, *, workers: int = WORKERS, chunk: int = CHUNK, opener=None) -> None:
        self.path = path
        self.chunk = chunk
        self.size = os.path.getsize(path)
        self.n_chunks = max(1, (self.size + chunk - 1) // chunk)
        self._opener = opener or (lambda p: open(p, "rb", buffering=0))  # noqa: SIM115 (시험에서 바꿔 끼움)
        self._chunks: dict[int, bytes] = {}
        self._inflight: set[int] = set()
        self._failed: dict[int, str] = {}
        self._pos = 0
        self._cv = threading.Condition()
        self.closed = False
        self._cancelled = False
        self._suspended = False
        self.full = False                 # True: 끝까지 받아 두기 (재생 자리 우선, 남는 틈에 나머지)
        self._full_next = 0
        self._done: set[int] = set()      # 한 번이라도 받은 조각 (드라이브가 이 컴퓨터에 보관)
        self.stats: dict[str, Any] = {"fetched": 0, "bytes": 0, "retries": 0, "secs": 0.0, "waits": 0,
                                      "wait_secs": 0.0, "errors": []}
        self._local = threading.local()
        self._threads = [threading.Thread(target=self._worker, name=f"cloud-{i}", daemon=True) for i in range(workers)]
        for t in self._threads:
            t.start()

    # ------------------------------------------------------------------ mpv 쪽
    def read_into(self, buf_addr: int, size: int) -> int:
        """mpv 버퍼에 최대 size 바이트를 넣고 넣은 수를 돌려준다 (0 = 끝, -1 = 오류)."""
        data = self.read_bytes(size)
        if data is None:
            return -1
        if data:
            ctypes.memmove(buf_addr, data, len(data))
        return len(data)

    def read_bytes(self, size: int) -> bytes | None:
        """지금 자리에서 최대 size 바이트 (조각 하나 안에서). None = 오류, b'' = 끝."""
        t0 = None
        with self._cv:
            while True:
                if self.closed or self._cancelled:
                    return None
                pos = self._pos
                if pos >= self.size:
                    return b""
                idx = pos // self.chunk
                data = self._chunks.get(idx)
                if data is not None:
                    break
                if idx in self._failed:
                    return None
                if t0 is None:
                    t0 = time.monotonic()
                    self.stats["waits"] += 1
                elif time.monotonic() - t0 > READ_TIMEOUT:
                    self.stats["errors"].append(f"{idx}번 조각 {READ_TIMEOUT:.0f}초 기다림")
                    return None
                self._cv.notify_all()          # 일꾼에게 '지금 이 조각' 을 알림
                self._cv.wait(0.25)
            if t0 is not None:
                self.stats["wait_secs"] += time.monotonic() - t0
                _log(f"기다림 {idx}번 조각 {time.monotonic() - t0:.2f}초")
            start = pos - idx * self.chunk
            out = data[start:start + max(0, int(size))]
            self._pos = pos + len(out)
            if self._pos // self.chunk != idx:          # 다음 조각으로 넘어감 → 정리하고 일꾼 깨우기
                self._evict()
                self._cv.notify_all()
            return out

    def seek(self, pos: int) -> int:
        _log(f"seek {self._pos >> 20}MB → {int(pos) >> 20}MB")
        with self._cv:
            self._pos = max(0, min(int(pos), self.size))
            self._evict()
            self._cv.notify_all()
            return self._pos

    def cancel(self) -> None:
        with self._cv:
            self._cancelled = True
            self._cv.notify_all()

    def close(self) -> None:
        with self._cv:
            self.closed = True
            self._chunks.clear()
            self._cv.notify_all()

    # ------------------------------------------------------------------ 앱 쪽
    def suspend(self) -> None:
        """새 조각 받기를 멈춘다 (통째로 받기 전에: 다른 읽기가 있으면 드라이브가 느린 길로 간다)."""
        with self._cv:
            self._suspended = True
            self._cv.notify_all()

    def resume(self) -> None:
        with self._cv:
            self._suspended = False
            self._cv.notify_all()

    def wait_idle(self, timeout: float = 15.0) -> bool:
        """받는 중인 조각이 다 끝날 때까지 기다린다."""
        end = time.monotonic() + timeout
        with self._cv:
            while self._inflight and time.monotonic() < end:
                self._cv.wait(0.2)
            return not self._inflight

    def start_full(self) -> None:
        """끝까지 받아 두기: 재생 자리 조각을 먼저, 남는 틈에 나머지를 차례로 (메모리엔 두지 않음)."""
        with self._cv:
            self.full = True
            self._cv.notify_all()

    @property
    def fetched_fraction(self) -> float:
        return len(self._done) / self.n_chunks

    def rate(self) -> float:
        """부분 읽기 속도 (바이트/초, 조각 3개 이상 받은 뒤의 실측. 그 전엔 기본값)."""
        s = self.stats
        return s["bytes"] / s["secs"] if s["fetched"] >= 3 and s["secs"] > 0 else PARTIAL_RATE_DEFAULT

    @property
    def buffered_ahead(self) -> int:
        """읽는 자리 앞으로 이어서 받아 둔 바이트 수."""
        with self._cv:
            i, n = self._pos // self.chunk, 0
            while i in self._chunks:
                n += len(self._chunks[i])
                i += 1
            return max(0, n - (self._pos % self.chunk))

    # ------------------------------------------------------------------ 일꾼
    def _evict(self) -> None:
        cur = self._pos // self.chunk
        lo, hi = cur - BEHIND, cur + AHEAD
        for i in [i for i in self._chunks if not (lo <= i <= hi or i in (0, self.n_chunks - 1))]:
            del self._chunks[i]

    def _pick(self) -> tuple[int, bool] | None:
        """다음에 받을 조각과 메모리에 둘지 여부. 잠금 안에서 부른다."""
        if self._suspended:
            return None
        cur = self._pos // self.chunk
        if cur < self.n_chunks and cur not in self._chunks and cur not in self._inflight and cur not in self._failed:
            return cur, True                                   # 1) 지금 mpv 가 기다리는 조각
        if self._inflight:                                     # 앞서 받기·끝까지 받기는 한 갈래만
            return None
        for i in range(cur + 1, min(self.n_chunks, cur + 1 + AHEAD)):
            if i not in self._chunks and i not in self._failed:
                return i, True                                 # 2) 앞서 받기
        if self.full:                                          # 3) 끝까지 받아 두기
            while self._full_next < self.n_chunks:
                i = self._full_next
                self._full_next += 1
                if i not in self._done:
                    return i, False
        return None

    def _worker(self) -> None:
        while True:
            with self._cv:
                job = None
                while not self.closed and not self._cancelled:
                    job = self._pick()
                    if job is not None:
                        break
                    self._cv.wait(0.5)
                if job is None:
                    self._close_file()
                    return
                idx, keep = job
                self._inflight.add(idx)
            try:
                data, err = self._fetch(idx), ""
            except Exception as e:  # noqa: BLE001
                data, err = None, str(e)
            with self._cv:
                self._inflight.discard(idx)
                if data is None:
                    self._failed[idx] = err
                    self.stats["errors"].append(f"{idx}번 조각: {err}")
                else:
                    self._done.add(idx)
                    cur = self._pos // self.chunk
                    if keep and (cur - BEHIND <= idx <= cur + AHEAD or idx in (0, self.n_chunks - 1)):
                        self._chunks[idx] = data
                self._cv.notify_all()

    def _file(self):
        f = getattr(self._local, "f", None)
        if f is None:
            f = self._local.f = self._opener(self.path)
        return f

    def _close_file(self) -> None:
        f = getattr(self._local, "f", None)
        if f is not None:
            try:
                f.close()
            except OSError:
                pass
            self._local.f = None

    def _fetch(self, idx: int) -> bytes:
        off = idx * self.chunk
        n = min(self.chunk, self.size - off)
        delay = RETRY_DELAY
        last: Exception | None = None
        for _ in range(RETRIES):
            if self.closed or self._cancelled:
                raise RuntimeError("닫힘")
            t = time.monotonic()
            try:
                f = self._file()
                f.seek(off)
                buf = bytearray(n)
                view = memoryview(buf)
                got = 0
                while got < n:
                    k = f.readinto(view[got:])
                    if not k:
                        break
                    got += k
                if got < n:
                    raise OSError(f"파일이 짧음 ({got}/{n})")
                dt = time.monotonic() - t
                self.stats["fetched"] += 1
                self.stats["bytes"] += n
                self.stats["secs"] += dt
                _log(f"받음 {idx}번 {dt:.2f}초 (읽는 자리 {self._pos // self.chunk}번)")
                return bytes(buf)
            except OSError as e:
                # 드라이브가 바쁠 때 ETIMEDOUT(60) 등을 돌려준다 → 파일을 다시 열고 잠시 뒤 다시
                last = e
                self.stats["retries"] += 1
                _log(f"다시 읽기 {idx}번: {e}")
                self._close_file()
                time.sleep(delay)
                delay = min(delay * 2, 4.0)
        raise OSError(f"{RETRIES}번 시도 실패: {last}")


# ---------------------------------------------------------------------- 받는 방식 고르기
def cloud_plan(size: int, duration: float, partial_rate: float, full_rate: float, fast_ok: bool) -> str:
    """드라이브 파일을 어떻게 볼지: "stream" | "fetch" | "stream_full".

    stream      : 받아 오면서 그대로 (영상에 필요한 속도가 드라이브 부분 읽기 속도의 85% 이하)
    fetch       : 첫 화면에서 잠깐 멈추고 빠른 길로 통째로 받은 뒤 재생 (맥) — 그냥 볼 때 예상되는 멈춤보다
                  통째로 받는 시간이 짧거나 20초 이내일 때
    stream_full : 받아 오면서 보되 뒤에서 끝까지 받아 둔다 (윈도우, 또는 너무 커서 통째로 받기가 더 오래 걸릴 때)
    """
    if duration <= 0 or size <= 0:
        return "stream"
    ratio = (size / duration) / max(1.0, partial_rate)
    if ratio <= 0.85:
        return "stream"
    stall = duration * (ratio - 0.85)          # 그냥 볼 때 예상되는 기다림 합계 (초, 어림)
    if fast_ok and size / max(1.0, full_rate) <= max(20.0, stall):
        return "fetch"
    return "stream_full"


# ---------------------------------------------------------------------- 통째로 받기 (맥 빠른 길)
def can_fetch_whole_fast() -> bool:
    return sys.platform == "darwin"


def fetch_whole_fast(path: str, scratch_dir: str) -> tuple[bool, float, str]:
    """맥: 드라이브 파일을 APFS 복제(clonefile)로 요청해 드라이브가 빠른 길로 통째로 받게 한다.

    복제본은 원본과 디스크 블록을 함께 쓰므로 공간을 거의 안 쓰고, 바로 지운다. 받은 내용은 드라이브가 보관한다.
    scratch_dir 은 같은 디스크(볼륨)여야 한다 (사용자 Library/Caches). 돌려주는 값: (성공, 걸린 초, 오류)
    """
    t = time.monotonic()
    if sys.platform != "darwin":
        return False, 0.0, "맥 전용"
    os.makedirs(scratch_dir, exist_ok=True)
    dst = os.path.join(scratch_dir, f"cp-whole-{uuid.uuid4().hex[:8]}{Path(path).suffix}")
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        fn = libc.clonefile
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
        fn.restype = ctypes.c_int
        rc = fn(os.fsencode(path), os.fsencode(dst), 0)
        if rc != 0:
            e = ctypes.get_errno()
            return False, time.monotonic() - t, f"clonefile 실패: {os.strerror(e)} ({e})"
        return True, time.monotonic() - t, ""
    except Exception as e:  # noqa: BLE001
        return False, time.monotonic() - t, str(e)
    finally:
        try:
            os.unlink(dst)
        except OSError:
            pass


# ---------------------------------------------------------------------- mpv 등록
class CloudStreams:
    """mpv 에 cpcloud:// 를 등록하고, 주소 ↔ 파일·읽기 장치를 관리한다."""

    def __init__(self, player) -> None:
        import mpv as _m
        self._m = _m
        self._paths: dict[str, str] = {}
        self.readers: dict[str, CloudReader] = {}
        self.current: CloudReader | None = None
        self._open_cb = _m.StreamOpenFn(self._open)       # 끝까지 참조를 쥐고 있어야 한다
        _m._mpv_stream_cb_add_ro(player.handle, PROTO.encode(), ctypes.c_void_p(), self._open_cb)  # noqa: SLF001

    def url_for(self, path: str) -> str:
        token = uuid.uuid4().hex[:12]
        self._paths[token] = path
        # 끝 부분 이름은 mpv 가 형식을 짐작하는 데만 쓴다 (화면 제목은 force-media-title)
        ext = os.path.splitext(path)[1].lower() or ".mp4"
        return f"{PROTO}://{token}/video{ext}"

    def reader_for(self, path: str) -> CloudReader | None:
        r = self.current
        return r if (r is not None and not r.closed and r.path == path) else None

    def _open(self, _ud, uri, info) -> int:
        try:
            token = uri.decode("utf-8", "replace").split("://", 1)[1].split("/", 1)[0]
            path = self._paths.get(token)
            if not path:
                return -13                                     # MPV_ERROR_LOADING_FAILED
            old = self.readers.get(token)
            reader = old if (old is not None and not old.closed) else CloudReader(path)
            self.readers[token] = reader
            self.current = reader
            m = self._m

            def _read(_c, buf, n):
                try:
                    return reader.read_into(ctypes.cast(buf, ctypes.c_void_p).value, n)
                except Exception:  # noqa: BLE001  (콜백 안 예외는 0=끝 으로 읽혀 영상이 끊긴다)
                    return -1

            def _seek(_c, off):
                try:
                    return reader.seek(off)
                except Exception:  # noqa: BLE001
                    return -1

            def _size(_c):
                return reader.size

            def _close(_c):
                try:
                    reader.close()
                finally:
                    if self.readers.get(token) is reader:
                        self.readers.pop(token, None)

            def _cancel(_c):
                try:
                    reader.cancel()
                except Exception:  # noqa: BLE001
                    pass

            cbs = (m.StreamReadFn(_read), m.StreamSeekFn(_seek), m.StreamSizeFn(_size),
                   m.StreamCloseFn(_close), m.StreamCancelFn(_cancel))
            reader._cbs = cbs                                  # 닫힐 때까지 참조 유지
            c = info.contents
            c.cookie = None
            c.read, c.seek, c.size, c.close, c.cancel = cbs
            return 0
        except Exception:  # noqa: BLE001
            return -13
