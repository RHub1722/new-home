"""Командная строка: homealize init-db | run."""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from homealize.config import Settings, load_settings
from homealize.logging_setup import configure_logging
from homealize.pipeline import run_once
from homealize.sources.nine99.source import build_nine99_source
from homealize.storage.db import connect, migrate

DEFAULT_CONFIG = Path("config/settings.yaml")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(prog="homealize", description="Мониторинг участков под строительство на 999.md")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="путь к settings.yaml")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="создать или обновить базу")
    commands.add_parser("run", help="один прогон: собрать объявления и сохранить")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        print(f"Не удалось прочитать настройки {args.config}: {exc}", file=sys.stderr)
        return 2
    if args.command == "init-db":
        return _init_db(settings)
    return _run(settings)


def _init_db(settings: Settings) -> int:
    conn = connect(settings.storage.db_path)
    try:
        version = migrate(conn)
    finally:
        conn.close()
    print(f"База готова: {settings.storage.db_path} (схема v{version})")
    return 0


def _run(settings: Settings) -> int:
    configure_logging(settings.logging)
    conn = connect(settings.storage.db_path)
    source = build_nine99_source(settings)
    try:
        migrate(conn)
        summary = run_once(conn, source, settings, now=datetime.now(UTC))
    finally:
        source.close()
        conn.close()
    state = "успешен" if summary.search_complete else "НЕ завершён"
    print(
        f"Прогон {state}: увидено {summary.ads_seen}, новых {summary.new}, "
        f"цена изменилась {summary.price_changed}, обновлено {summary.changed}, "
        f"снято {summary.removed}, ошибок {len(summary.errors)}"
    )
    return 0 if summary.search_complete else 1


def _force_utf8_output() -> None:
    """В Windows вывод в пайп идёт в кодировке системы; кириллица без этого может упасть."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


if __name__ == "__main__":
    sys.exit(main())
