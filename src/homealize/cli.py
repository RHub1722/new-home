"""Командная строка: homealize init-db | run | rescore | export | refresh-poi | serve | digest | telegram-test."""

import argparse
import html
import logging
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import yaml

from homealize.config import Settings, load_settings, load_telegram_secrets
from homealize.enrich.poi import ensure_pois
from homealize.evaluate import build_http_client
from homealize.export.excel import ExportError, export_workbook
from homealize.jobs import CycleResult, Evaluation, evaluate_and_export, run_cycle, send_digest
from homealize.logging_setup import configure_logging
from homealize.models import RunSummary
from homealize.notify import outbox
from homealize.notify.digest import digest_key
from homealize.notify.outbox import Delivery, Notifier
from homealize.notify.telegram import TelegramClient, TelegramError
from homealize.runlock import LockBusyError, hold_lock
from homealize.serve import Service
from homealize.settlements import load_settlements
from homealize.sources.nine99.source import build_nine99_source
from homealize.storage.db import connect, migrate

log = logging.getLogger(__name__)

DEFAULT_CONFIG = Path("config/settings.yaml")
SCAN_INTERRUPTED = 1
BAD_SETTINGS = 2
EVALUATION_FAILED = 3  # оценка, Excel, обновление данных или Telegram не удались
BUSY = 4
SERVE_CRASHED = 1  # serve остановился из-за неожиданной ошибки
BUSY_MESSAGE = "Другой прогон ещё идёт (например, по расписанию serve) — попробуйте через пару минут."
DIGEST_QUEUED_MESSAGE = "Идёт другой прогон — дайджест поставлен в очередь и уйдёт со следующей отправкой."
NO_TELEGRAM = (
    "Telegram не настроен: нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env "
    "(см. README, раздел «Telegram», и `uv run homealize telegram-test`)."
)
NEEDS_SETTLEMENTS = {"run", "rescore", "serve"}


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(prog="homealize", description="Мониторинг участков под строительство на 999.md")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="путь к settings.yaml")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="создать или обновить базу")
    run = commands.add_parser("run", help="обход сайта, оценка, Excel и уведомления")
    run.add_argument("--dry-run", action="store_true", help="уведомления не отправлять, а показать")
    commands.add_parser("rescore", help="пересчитать баллы и выгрузить Excel без обхода сайта")
    commands.add_parser("export", help="выгрузить Excel из уже посчитанных баллов")
    commands.add_parser("refresh-poi", help="заново скачать инфраструктуру из OpenStreetMap")
    commands.add_parser("serve", help="прогоны по расписанию и Telegram-бот (работает, пока не остановят)")
    digest = commands.add_parser("digest", help="отправить дайджест дня сейчас")
    digest.add_argument("--dry-run", action="store_true", help="не отправлять, а показать")
    commands.add_parser("telegram-test", help="проверить бота и узнать chat id")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config)
    except (OSError, ValueError, yaml.YAMLError) as exc:  # ValueError: не UTF-8 и ошибки pydantic
        print(f"Не удалось прочитать настройки {args.config}: {exc}", file=sys.stderr)
        return BAD_SETTINGS
    if args.command in NEEDS_SETTLEMENTS:
        try:
            load_settlements(settings.scoring.settlements_path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            print(f"Не удалось прочитать справочник сёл {settings.scoring.settlements_path}: {exc}", file=sys.stderr)
            return BAD_SETTINGS
    handlers: dict[str, Callable[[], int]] = {
        "init-db": lambda: _init_db(settings),
        "run": lambda: _run(settings, dry_run=args.dry_run),
        "rescore": lambda: _rescore(settings),
        "export": lambda: _export(settings),
        "refresh-poi": lambda: _refresh_poi(settings),
        "serve": lambda: _serve(settings),
        "digest": lambda: _digest(settings, dry_run=args.dry_run),
        "telegram-test": lambda: _telegram_test(settings),
    }
    return handlers[args.command]()


def _init_db(settings: Settings) -> int:
    conn = connect(settings.storage.db_path)
    try:
        version = migrate(conn)
    finally:
        conn.close()
    print(f"База готова: {settings.storage.db_path} (схема v{version})")
    return 0


def _run(settings: Settings, *, dry_run: bool) -> int:
    configure_logging(settings.logging)
    try:
        notifier = None if dry_run else _notifier(settings)
    except ValueError as exc:
        print(f"Не удалось прочитать {settings.telegram.env_file}: {exc}", file=sys.stderr)
        return BAD_SETTINGS
    conn = connect(settings.storage.db_path)
    try:
        migrate(conn)
        result = run_cycle(
            conn,
            settings,
            source_factory=lambda: build_nine99_source(settings),
            notifier=notifier,
            now=_utc_now(),
            clock=_utc_now,
            dry_run=dry_run,
        )
    except LockBusyError:
        print(BUSY_MESSAGE, file=sys.stderr)
        return BUSY
    finally:
        conn.close()
        if notifier is not None:
            notifier.client.close()
    _print_cycle(result, telegram=notifier is not None, dry_run=dry_run)
    if result.scan is None or not result.scan.search_complete:
        return SCAN_INTERRUPTED
    if result.evaluation is None or not result.evaluation.ok:
        return EVALUATION_FAILED
    delivery = result.delivery
    return EVALUATION_FAILED if delivery is not None and (delivery.failed or delivery.dropped) else 0


def _rescore(settings: Settings) -> int:
    configure_logging(settings.logging)
    conn = connect(settings.storage.db_path)
    try:
        migrate(conn)
        with hold_lock(settings.storage.run_lock_path):
            evaluation = evaluate_and_export(conn, settings, now=_utc_now())
    except LockBusyError:
        print(BUSY_MESSAGE, file=sys.stderr)
        return BUSY
    finally:
        conn.close()
    _print_evaluation(evaluation)
    return 0 if evaluation.ok else EVALUATION_FAILED


def _export(settings: Settings) -> int:
    conn = connect(settings.storage.db_path)
    try:
        migrate(conn)
        path = export_workbook(conn, settings.export, now=_utc_now())
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
            pois, warning = ensure_pois(conn, http, settings.poi, now=_utc_now(), force=True)
    finally:
        conn.close()
    if warning:
        print(warning, file=sys.stderr)
        return EVALUATION_FAILED
    print(f"Инфраструктура обновлена: {len(pois)} объектов")
    return 0


def _serve(settings: Settings) -> int:
    configure_logging(settings.logging)
    try:
        secrets = load_telegram_secrets(settings.telegram.env_file)
    except ValueError as exc:
        return _serve_refused(f"Не удалось прочитать {settings.telegram.env_file}: {exc}", BAD_SETTINGS)
    if secrets is None or secrets.chat_id is None:
        return _serve_refused(f"Для serve нужен Telegram. {NO_TELEGRAM}", BAD_SETTINGS)
    try:
        with hold_lock(settings.storage.serve_lock_path):
            return _serve_locked(settings, Notifier(_telegram_client(settings, secrets.token), secrets.chat_id))
    except LockBusyError:
        return _serve_refused("serve уже запущен (например, автозапуском) — второй не нужен.", BUSY)


def _serve_refused(message: str, code: int) -> int:
    """Отказ запускаться: в консоль и в лог (под pythonw консоли нет, и в автозапуске видно только лог)."""
    print(message, file=sys.stderr)
    log.error(message)
    return code


def _serve_locked(settings: Settings, notifier: Notifier) -> int:
    conn = None
    try:
        conn = connect(settings.storage.db_path)
        migrate(conn)

        def run_job(now: datetime) -> None:
            run_cycle(
                conn,
                settings,
                source_factory=lambda: build_nine99_source(settings),
                notifier=notifier,
                now=now,
                clock=_utc_now,
            )

        log.info("serve запущен")
        Service(conn, settings, notifier, run_job=run_job).run_forever()
    except KeyboardInterrupt:
        log.info("serve остановлен")
    except Exception:
        log.exception("serve остановлен из-за ошибки")
        return SERVE_CRASHED
    finally:
        if conn is not None:
            conn.close()
        notifier.client.close()
    return 0


def _digest(settings: Settings, *, dry_run: bool) -> int:
    configure_logging(settings.logging)
    try:
        notifier = None if dry_run else _notifier(settings)
    except ValueError as exc:
        print(f"Не удалось прочитать {settings.telegram.env_file}: {exc}", file=sys.stderr)
        return BAD_SETTINGS
    if notifier is None and not dry_run:
        print(NO_TELEGRAM, file=sys.stderr)
        return BAD_SETTINGS
    now = _utc_now()
    conn = connect(settings.storage.db_path)
    try:
        migrate(conn)
        before = after = None
        if not dry_run:  # состояние сегодняшнего дайджеста до отправки и после неё
            key = digest_key(now.astimezone(settings.schedule.zone).date())
            before = outbox.status(conn, key)
        item, _added, delivery = send_digest(conn, settings, notifier, now=now, dry_run=dry_run)
        if not dry_run:
            after = outbox.status(conn, item.key)
    finally:
        conn.close()
        if notifier is not None:
            notifier.client.close()
    if dry_run:
        print(_plain(item.text))
        return 0
    if before == "sent":
        print("Дайджест за сегодня уже отправлялся.")
        return 0
    if delivery is None and after != "dropped":  # Telegram настроен, не dry-run: блокировка прогона занята
        print(DIGEST_QUEUED_MESSAGE, file=sys.stderr)
        return BUSY
    if before == "pending":
        print("Дайджест за сегодня ждёт отправки.")
    if delivery is not None:
        _print_delivery(delivery)
    if after == "dropped":
        print(
            f"Дайджест за сегодня отправить не удалось — подробности в {settings.logging.path.as_posix()}.",
            file=sys.stderr,
        )
    failed = delivery is not None and (delivery.failed or delivery.dropped)
    return EVALUATION_FAILED if failed or after == "dropped" else 0


def _telegram_test(settings: Settings) -> int:
    try:
        secrets = load_telegram_secrets(settings.telegram.env_file)
    except ValueError as exc:
        print(f"Не удалось прочитать {settings.telegram.env_file}: {exc}", file=sys.stderr)
        return BAD_SETTINGS
    if secrets is None:
        print(
            "Токена бота нет. Создайте бота у @BotFather в Telegram (команда /newbot) и запишите токен "
            "в .env: TELEGRAM_BOT_TOKEN=...",
            file=sys.stderr,
        )
        return BAD_SETTINGS
    with _telegram_client(settings, secrets.token) as client:
        try:
            username = client.get_me().get("username", "?")
            if secrets.chat_id is None:
                return _print_chats(username, client.get_updates(None, 0))
            client.send_message(secrets.chat_id, "✅ Homealize на связи. Команды: /help")
        except TelegramError as exc:
            print(f"Telegram: {exc}", file=sys.stderr)
            return EVALUATION_FAILED
    print(f"Бот @{username} отправил тестовое сообщение в чат {secrets.chat_id}.")
    return 0


def _print_chats(username: str, updates: list[dict]) -> int:
    chats: dict[int, str] = {}
    for update in updates:
        message = update.get("message") if isinstance(update.get("message"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        if isinstance(chat.get("id"), int):
            chats[chat["id"]] = chat.get("username") or chat.get("first_name") or chat.get("title") or ""
    if not chats:
        print(f"Бот @{username} на связи, но ему ещё никто не писал. Напишите боту что-нибудь и повторите команду.")
        return BAD_SETTINGS
    print(f"Бот @{username} на связи. Чаты, которые ему писали:")
    for chat_id, name in chats.items():
        print(f"  {chat_id} {name}".rstrip())
    print("Запишите нужный в .env: TELEGRAM_CHAT_ID=<число> — и повторите команду.")
    return BAD_SETTINGS


def _notifier(settings: Settings) -> Notifier | None:
    """Telegram из .env; None — не настроен. ValueError — .env испорчен."""
    secrets = load_telegram_secrets(settings.telegram.env_file)
    if secrets is None or secrets.chat_id is None:
        return None
    return Notifier(_telegram_client(settings, secrets.token), secrets.chat_id)


def _telegram_client(settings: Settings, token: str) -> TelegramClient:
    return TelegramClient(token, api_url=settings.telegram.api_url, timeout_seconds=settings.telegram.timeout_seconds)


def _print_cycle(result: CycleResult, *, telegram: bool, dry_run: bool) -> None:
    if result.scan is not None:
        _print_scan(result.scan)
    if result.crash is not None:
        print(f"Прогон упал: {result.crash}", file=sys.stderr)
    if result.evaluation is not None:
        _print_evaluation(result.evaluation)
    if dry_run:
        print(f"--dry-run: уведомлений к отправке {len(result.planned)}, они не отправлены.")
        for item in result.planned:
            print(f"--- {item.key}")
            print(_plain(item.text))
    elif not telegram:
        print("Telegram не настроен — уведомления не отправлены (см. README, раздел «Telegram»).")
    elif result.delivery is not None:
        _print_delivery(result.delivery)


def _print_evaluation(evaluation: Evaluation) -> None:
    result = evaluation.summary
    if result is not None:
        best = f"{result.best_total:.0f}" if result.best_total is not None else "—"
        line = f"Оценка: оценено {result.scored}, в рейтинге {result.eligible}, лучший балл {best}."
        print(f"{line} Excel: {evaluation.path}" if evaluation.path is not None else line)
        for warning in result.warnings:
            print(f"Предупреждение: {warning}")
    if evaluation.failure is not None:
        print(evaluation.failure.detail, file=sys.stderr)


def _print_delivery(delivery: Delivery) -> None:
    line = f"Telegram: отправлено {delivery.sent}"
    if delivery.failed:
        line += f", не удалось {delivery.failed} (повторю в следующий раз)"
    if delivery.dropped:
        line += f", отброшено {delivery.dropped}"
    print(line)


def _print_scan(summary: RunSummary) -> None:
    state = "успешен" if summary.search_complete else "НЕ завершён"
    print(
        f"Прогон {state}: увидено {summary.ads_seen}, новых {summary.new}, "
        f"цена изменилась {summary.price_changed}, обновлено {summary.changed}, "
        f"снято {summary.removed}, ошибок {len(summary.errors)}"
    )


def _plain(text: str) -> str:
    """HTML сообщения Telegram → текст для консоли."""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
