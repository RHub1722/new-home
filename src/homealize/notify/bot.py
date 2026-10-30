"""Команды бота (спека §7.1): /top, /new, /ad, /fav, /unfav, /favs, /hide, /unhide, /status, /help.

Бот отвечает только в чат TELEGRAM_CHAT_ID. Отметки ★ и «скрыть» сохраняются сразу, а «скрыть»
тут же меняет места в снимке оценки.
"""

import json
import logging
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape

from homealize.config import Settings
from homealize.notify import format as fmt
from homealize.notify import outbox
from homealize.notify.outbox import Notifier
from homealize.notify.telegram import TelegramError
from homealize.storage import marks, repo
from homealize.storage.scores import apply_hidden, get_scored, load_scored

log = logging.getLogger(__name__)

COMMAND = re.compile(r"^/([a-z]+)(?:_(\w+))?(?:@\w+)?(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
LISTING_ID = re.compile(r"\d{4,}")
FILTER_LABELS = {
    "land_type": "не «под строительство»",
    "area": "площадь вне диапазона",
    "region": "район вне списка",
    "no_price": "без цены",
}
STATES = {"ok": "✅ успешен", "failed": "❌ не завершён", "running": "⏳ идёт"}
HELP = "\n".join([
    "<b>Homealize</b> — участки под строительство на 999.md.",
    "/top [N] — лучшие N участков (по умолчанию 10)",
    "/new — новые за сутки",
    "/ad &lt;id&gt; — карточка участка",
    "/fav &lt;id&gt;, /unfav &lt;id&gt;, /favs — избранное ★",
    "/hide &lt;id&gt;, /unhide &lt;id&gt; — убрать из рейтинга и вернуть",
    "/status — последний прогон",
    "Вместо id можно прислать ссылку на объявление.",
])


@dataclass(frozen=True)
class Reply:
    text: str
    preview_url: str | None = None


@dataclass(frozen=True)
class _Context:
    conn: sqlite3.Connection
    settings: Settings
    now: datetime
    next_run: datetime | None


def handle_update(
    conn: sqlite3.Connection,
    settings: Settings,
    notifier: Notifier,
    update: dict,
    *,
    now: datetime,
    next_run: datetime | None = None,
) -> None:
    """Отвечает на одно сообщение боту. Ошибки команды и отправки пишутся в лог, наружу не выходят."""
    message = update.get("message")
    if not isinstance(message, dict):
        return
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    if chat.get("id") != notifier.chat_id:
        log.info("Сообщение из чужого чата %s пропущено", chat.get("id"))
        return
    text = message.get("text")
    if not isinstance(text, str):
        return
    try:
        reply = answer(conn, settings, text, now=now, next_run=next_run)
    except Exception:
        conn.rollback()
        log.exception("Команда боту не выполнена")
        reply = Reply("Не получилось выполнить команду — подробности в logs/homealize.log.")
    try:
        notifier.client.send_message(notifier.chat_id, reply.text, preview_url=reply.preview_url)
    except TelegramError as exc:
        log.warning("Ответ боту не отправлен: %s", exc)


def answer(
    conn: sqlite3.Connection, settings: Settings, text: str, *, now: datetime, next_run: datetime | None = None
) -> Reply:
    match = COMMAND.match(text.strip())
    if match is None:
        return Reply(HELP)
    name = match.group(1).lower()
    argument = (match.group(2) or match.group(3) or "").strip()
    handler = COMMANDS.get(name)
    if handler is None:
        return Reply(f"Не знаю команду /{escape(name)}.\n\n{HELP}")
    return handler(_Context(conn, settings, now, next_run), argument)


def _help(ctx: _Context, argument: str) -> Reply:
    return Reply(HELP)


def _top(ctx: _Context, argument: str) -> Reply:
    t = ctx.settings.telegram
    if argument and not argument.isdigit():
        return Reply("После /top нужно число, например: /top 20")
    count = max(1, min(int(argument) if argument else t.top_default, t.top_max))
    rows = [row for row in load_scored(ctx.conn) if row["rank"] is not None][:count]
    if not rows:
        return Reply("В рейтинге пока пусто: дождитесь прогона.")
    favs = marks.marked(ctx.conn, marks.FAV)
    lines = [f"<b>Топ-{len(rows)}</b>", *[fmt.listing_line(row, fav=row["listing_id"] in favs) for row in rows]]
    return Reply("\n".join(lines))


def _new(ctx: _Context, argument: str) -> Reply:
    since = (ctx.now.astimezone(UTC) - timedelta(days=1)).isoformat()
    fresh = [row for row in load_scored(ctx.conn) if row["first_seen_at"] >= since]
    if not fresh:
        return Reply("За сутки новых участков нет.")
    rated = [row for row in fresh if row["rank"] is not None]
    favs = marks.marked(ctx.conn, marks.FAV)
    lines = [f"<b>Новые за сутки</b>: {len(fresh)}, в рейтинге {len(rated)}"]
    lines += [fmt.listing_line(row, fav=row["listing_id"] in favs) for row in rated[: ctx.settings.telegram.top_max]]
    others = len(fresh) - len(rated)
    if others:
        lines.append(f"Ещё {others} вне рейтинга (дороже бюджета, далеко, дубликаты…) — в Excel, лист «Все активные».")
    return Reply("\n".join(lines))


def _ad(ctx: _Context, argument: str) -> Reply:
    listing_id, error = _known_id(ctx.conn, argument, "ad")
    if error:
        return Reply(error)
    row = get_scored(ctx.conn, listing_id)
    if row is None:
        return Reply(_unscored(ctx.conn, listing_id))
    fav = listing_id in marks.marked(ctx.conn, marks.FAV)
    history = repo.get_price_history(ctx.conn, listing_id)
    return Reply(fmt.card(row, history, fav=fav, zone=ctx.settings.schedule.zone), preview_url=fmt.first_photo(row))


def _fav(ctx: _Context, argument: str) -> Reply:
    listing_id, error = _known_id(ctx.conn, argument, "fav")
    if error:
        return Reply(error)
    added = marks.add_mark(ctx.conn, listing_id, marks.FAV, now=ctx.now)
    ctx.conn.commit()
    name = _name(ctx.conn, listing_id)
    return Reply(f"★ В избранном: {name}" if added else f"Уже в избранном: {name}")


def _unfav(ctx: _Context, argument: str) -> Reply:
    listing_id, error = _known_id(ctx.conn, argument, "unfav")
    if error:
        return Reply(error)
    removed = marks.remove_mark(ctx.conn, listing_id, marks.FAV)
    ctx.conn.commit()
    name = _name(ctx.conn, listing_id)
    return Reply(f"Убрано из избранного: {name}" if removed else f"Этого участка нет в избранном: {name}")


def _favs(ctx: _Context, argument: str) -> Reply:
    ids = sorted(marks.marked(ctx.conn, marks.FAV))
    if not ids:
        return Reply("Избранного пока нет: отметьте участок командой /fav &lt;id&gt;.")
    lines = [f"<b>Избранное</b>: {len(ids)}"]
    for listing_id in ids:
        row = get_scored(ctx.conn, listing_id)
        lines.append(fmt.listing_line(row, fav=True) if row is not None else f"★ {_unscored(ctx.conn, listing_id)}")
    return Reply("\n".join(lines))


def _hide(ctx: _Context, argument: str) -> Reply:
    listing_id, error = _known_id(ctx.conn, argument, "hide")
    if error:
        return Reply(error)
    added = marks.add_mark(ctx.conn, listing_id, marks.HIDDEN, now=ctx.now)
    apply_hidden(ctx.conn, marks.marked(ctx.conn, marks.HIDDEN))
    ctx.conn.commit()
    name = _name(ctx.conn, listing_id)
    if not added:
        return Reply(f"Уже скрыто: {name}")
    return Reply(f"Скрыто: {name}. В рейтинге и уведомлениях его больше не будет. Вернуть: /unhide_{listing_id}")


def _unhide(ctx: _Context, argument: str) -> Reply:
    listing_id, error = _known_id(ctx.conn, argument, "unhide")
    if error:
        return Reply(error)
    removed = marks.remove_mark(ctx.conn, listing_id, marks.HIDDEN)
    apply_hidden(ctx.conn, marks.marked(ctx.conn, marks.HIDDEN))
    ctx.conn.commit()
    name = _name(ctx.conn, listing_id)
    if not removed:
        return Reply(f"Этот участок не был скрыт: {name}")
    row = get_scored(ctx.conn, listing_id)
    place = f", место {row['rank']}" if row is not None and row["rank"] is not None else ""
    return Reply(f"Возвращено в рейтинг: {name}{place}")


def _status(ctx: _Context, argument: str) -> Reply:
    run = repo.last_run(ctx.conn)
    if run is None:
        return Reply("Прогонов ещё не было.")
    zone = ctx.settings.schedule.zone
    head = f"<b>Последний прогон</b>: {fmt.local_time(run['started_at'], zone)}, {STATES.get(run['status'], run['status'])}"
    if run["finished_at"]:
        seconds = (datetime.fromisoformat(run["finished_at"]) - datetime.fromisoformat(run["started_at"])).total_seconds()
        head += f", {int(seconds // 60)} мин {int(seconds % 60)} с"
    lines = [
        head,
        f"Увидено {run['ads_seen']}, новых {run['new']}, цена изменилась {run['price_changed']}, "
        f"обновлено {run['changed']}, снято {run['removed']}",
    ]
    errors = json.loads(run["errors"])
    if errors:
        lines.append(f"Ошибки и предупреждения ({len(errors)}):")
        lines += [f"• {escape(fmt.short(error))}" for error in errors[:5]]
        if len(errors) > 5:
            lines.append(f"• …и ещё {len(errors) - 5}")
    rated = ctx.conn.execute("SELECT COUNT(*) FROM scores WHERE rank IS NOT NULL").fetchone()[0]
    lines.append(f"В рейтинге: {rated}")
    pending = outbox.pending_count(ctx.conn)
    if pending:
        lines.append(f"Ждут отправки уведомлений: {pending}")
    if ctx.next_run is not None:
        lines.append(f"Следующий прогон: {fmt.local_time(ctx.next_run, zone)}")
    return Reply("\n".join(lines))


COMMANDS: dict[str, Callable[[_Context, str], Reply]] = {
    "start": _help,
    "help": _help,
    "top": _top,
    "new": _new,
    "ad": _ad,
    "fav": _fav,
    "unfav": _unfav,
    "favs": _favs,
    "hide": _hide,
    "unhide": _unhide,
    "status": _status,
}


def _known_id(conn: sqlite3.Connection, argument: str, command: str) -> tuple[str | None, str | None]:
    """(id, None) для объявления из базы, иначе (None, текст ошибки)."""
    match = LISTING_ID.search(argument)
    if match is None:
        return None, f"Укажите id объявления или ссылку на него, например: /{command} 100000001"
    listing_id = match.group(0)
    if repo.get_listing(conn, listing_id) is None:
        return None, f"Объявления {listing_id} нет в базе."
    return listing_id, None


def _name(conn: sqlite3.Connection, listing_id: str) -> str:
    listing = repo.get_listing(conn, listing_id)
    settlement = escape(listing["settlement"] or fmt.NO_SETTLEMENT)
    return f"{settlement}, {fmt.area(listing['area_sot'])} ({fmt.ad_command(listing_id)})"


def _unscored(conn: sqlite3.Connection, listing_id: str) -> str:
    listing = repo.get_listing(conn, listing_id)
    if listing["status"] == "removed":
        why = "снято с сайта"
    elif listing["status"] == "filtered_out":
        why = f"отсеяно фильтром: {FILTER_LABELS.get(listing['filter_reason'], listing['filter_reason'])}"
    else:
        why = "ещё не оценено"
    return f'{_name(conn, listing_id)} — не в оценке ({why}). <a href="{escape(listing["url"])}">999.md</a>'
