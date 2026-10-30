"""Логи: файл с ротацией и консоль."""

import logging
import sys
from logging.handlers import RotatingFileHandler

from homealize.config import LoggingSettings

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MARK = "_homealize_handler"


def configure_logging(cfg: LoggingSettings) -> None:
    reset_logging()
    cfg.path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(FORMAT)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(cfg.path, maxBytes=cfg.max_bytes, backupCount=cfg.backup_count, encoding="utf-8")
    ]
    if sys.stderr is not None:  # под pythonw (автозапуск) консоли нет
        handlers.append(logging.StreamHandler())
    root = logging.getLogger()
    for handler in handlers:
        handler.setFormatter(formatter)
        setattr(handler, _MARK, True)
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    # httpx пишет каждый запрос на уровне INFO: в логах и так есть строка на страницу.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def reset_logging() -> None:
    """Снимает и закрывает обработчики, добавленные configure_logging()."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _MARK, False):
            root.removeHandler(handler)
            handler.close()
    logging.getLogger("httpx").setLevel(logging.NOTSET)
