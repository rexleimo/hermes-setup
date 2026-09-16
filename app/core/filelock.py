"""跨平台独占文件锁。

POSIX 走 fcntl.flock；Windows 走 msvcrt.locking（锁定首字节，失败自旋重试）；
两者都不可用时降级为「无锁 + 原子替换」——os.replace 本身保证读者不会看到半成品，
丢掉的只是并发写的串行化，后台场景可接受。
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager

try:  # POSIX
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore

try:  # Windows
    import msvcrt  # type: ignore
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore


class LockTimeout(Exception):
    """在超时时间内没有拿到锁。"""


@contextmanager
def exclusive_lock(path: os.PathLike | str, *, timeout: float = 10.0):
    """对锁文件加独占锁；文件不存在则创建。"""
    fh = open(path, "a+b")
    try:
        if fcntl is not None:
            _lock_posix(fh, timeout)
        elif msvcrt is not None:
            _lock_windows(fh, timeout)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_UN)
            elif msvcrt is not None:
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
    finally:
        fh.close()


def _lock_posix(fh, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LockTimeout(f"获取文件锁超时：{fh.name}")
            time.sleep(0.05)


def _lock_windows(fh, timeout: float) -> None:
    # msvcrt 只能锁定有内容的字节，空文件先写入占位
    try:
        if os.fstat(fh.fileno()).st_size == 0:
            fh.write(b"\0")
            fh.flush()
    except OSError:
        pass
    deadline = time.monotonic() + timeout
    fh.seek(0)
    while True:
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise LockTimeout(f"获取文件锁超时：{fh.name}")
            time.sleep(0.05)
