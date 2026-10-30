"""Командная строка: homealize init-db | run | rescore | export | refresh-poi."""

import argparse
import logging
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from homealize.config import Settings, load_settings
from homealize.enrich.poi import ensure_pois
from homealize.evaluate import build_http_client, evaluate
from homealize.export.excel import ExportError, export_workbook
from homealize.logging_setup import configure_logging
from homealize.models import RunSummary
from homealize.pipeline import run_once
from homealize.scoring.engine import ScoreSummary
from homealize.sources.nine99.source import build_nine99_source
from homealize.storage import repo
from homealize.storage.db import connect, migrate

log = logging.getLogger(__name__)

DEFAULT_CONFIG = Path("config/settings.yaml")
EVALUATION_FAILED = 3


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(prog="homealize", description="Мониторинг участков под строительство на 999.md")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="путь к settings.yaml")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="создать или обновить базу")
    commands.add_parser("run", help="обход сайта, оценка участков и выгрузка Excel")
    commands.add_parser("rescore", help="пересчитать баллы и выгрузить Excel без обхода сайта")
    commands.add_parser("export", help="выгрузить Excel из уже посчитанных баллов")
    commands.add_parser("refresh-poi", help="заново скачать инфраструктуру из OpenStreetMap")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config)
    except (OSError, ValueError, yaml.YAMLError) as exc:  # ValueError: не UTF-8 и ошибки pydantic
        print(f"Не удалось прочитать настройки {args.config}: {exc}", file=sys.stderr)
        return 2
    handlers = {
        "init-db": _init_db,
        "run": _run,
        "rescore": _rescore,
        "export": _export,
        "refresh-poi": _refresh_poi,
    }
    return handlers[args.command](settings)


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
        now = datetime.now(UTC)
        summary = run_once(conn, source, settings, now=now)
        _print_scan(summary)
        evaluation = _evaluate_and_export(conn, settings, now=now)
        _log_to_last_run(conn, evaluation.messages)
    finally:
        source.close()
        conn.close()
    if not summary.search_complete:
        return 1
    return 0 if evaluation.ok else EVALUATION_FAILED


def _rescore(settings: Settings) -> int:
    configure_logging(settings.logging)
    conn = connect(settings.storage.db_path)
    try:
        migrate(conn)
        evaluation = _evaluate_and_export(conn, settings, now=datetime.now(UTC))
    finally:
        conn.close()
    return 0 if evaluation.ok else EVALUATION_FAILED


def _export(settings: Settings) -> int:
    conn = connect(settings.storage.db_path)
    try:
        migrate(conn)
        path = export_workbook(conn, settings.export, now=datetime.now(UTC))
    except ExportError as exc:
        print(f"Excel не сохранён: {exc}", file=sys.stderr)
        return EVALUATION_FAILED
    finally:
        conn.close()
    print(f"Excel: {path}")
    return 0


def _refresh_poi(settings: Settings) -> int:
    configure_logging(settings.logging)
    conn = connect(settings.storage.db_path)
    try:
        migrate(conn)
        with build_http_client() as http:
            pois, warning = ensure_pois(conn, http, settings.poi, now=datetime.now(UTC), force=True)
    finally:
        conn.close()
    if warning:
        print(warning, file=sys.stderr)
        return EVALUATION_FAILED
    print(f"Инфраструктура обновлена: {len(pois)} объектов")
    return 0


@dataclass(frozen=True)
class _Evaluation:
    """Итог оценки и выгрузки: удались ли они и что дописать в журнал прогона."""

    ok: bool
    messages: list[str]


def _evaluate_and_export(conn: sqlite3.Connection, settings: Settings, *, now: datetime) -> _Evaluation:
    try:
        with build_http_client() as http:
            result = evaluate(conn, settings, http, now=now)
        try:
            path = export_workbook(conn, settings.export, now=now)
        except ExportError as exc:
            message = f"Excel не сохранён: {exc}"
            _print_evaluation(result, path=None)
            print(message, file=sys.stderr)
            return _Evaluation(False, [*result.warnings, message])
    except Exception as exc:  # оценка не должна терять уже сохранённый обход: сообщаем и выходим с кодом 3
        conn.rollback()  # недописанное оценкой не должно попасть в базу вместе с записью в журнал прогона
        log.exception("Оценка не выполнена")
        message = f"Оценка не выполнена: {type(exc).__name__}: {exc}"
        print(message, file=sys.stderr)
        return _Evaluation(False, [message])
    _print_evaluation(result, path=path)
    return _Evaluation(True, list(result.warnings))


def _print_evaluation(result: ScoreSummary, *, path: Path | None) -> None:
    best = f"{result.best_total:.0f}" if result.best_total is not None else "—"
    line = f"Оценка: оценено {result.scored}, в рейтинге {result.eligible}, лучший балл {best}."
    print(f"{line} Excel: {path}" if path is not None else line)
    for warning in result.warnings:
        print(f"Предупреждение: {warning}")


def _log_to_last_run(conn: sqlite3.Connection, messages: list[str]) -> None:
    """Предупреждения и сбой оценки дописываются в errors прогона, который только что записал run_once."""
    if not messages:
        return
    repo.add_run_errors(conn, repo.last_run(conn)["id"], messages)
    conn.commit()


def _print_scan(summary: RunSummary) -> None:
    state = "успешен" if summary.search_complete else "НЕ завершён"
    print(
        f"Прогон {state}: увидено {summary.ads_seen}, новых {summary.new}, "
        f"цена изменилась {summary.price_changed}, обновлено {summary.changed}, "
        f"снято {summary.removed}, ошибок {len(summary.errors)}"
    )


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
