"""Блокировка файлом: пока идёт прогон (или работает serve), второй такой же не начнётся.

Блокировку держит операционная система: если процесс упал или его остановили, она снимается сама.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class LockBusyError(Exception):
    """Блокировку уже держит другой процесс (или другой вход в этом же процессе)."""


@contextmanager
def hold_lock(path: Path) -> Iterator[None]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            _lock(fd)
        except OSError as exc:
            raise LockBusyError(str(path)) from exc
        try:
            yield
        finally:
            with suppress(OSError):
                _unlock(fd)
    finally:
        os.close(fd)


def _lock(fd: int) -> None:
    if os.name == "nt":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if os.name == "nt":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
