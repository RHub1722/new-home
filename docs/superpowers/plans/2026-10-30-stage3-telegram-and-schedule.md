# Этап 3 «Telegram и расписание» — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** программа сама обходит 999.md по расписанию и присылает в Telegram новые подходящие участки, снижения цен, вечерний дайджест и технические тревоги; бот отвечает на команды `/top`, `/new`, `/ad`, `/fav`, `/hide`, `/status`; на ПК всё это запускается автоматически при входе в Windows.

**Architecture:**
- `jobs.run_cycle` — один цикл прогона под файловой блокировкой: обход (`pipeline.run_once`) → оценка и Excel (`evaluate`) → планирование уведомлений → очередь → отправка. Им пользуются и `homealize run`, и `homealize serve`.
- Уведомления разделены на «что сообщить» (`notify/events.py`, `notify/alerts.py`, `notify/digest.py` — чистые функции над базой) и «как доставить» (`notify/outbox.py` — очередь в таблице `notifications`, `notify/telegram.py` — клиент Bot API на httpx).
- `serve` — один поток: `scheduler.Schedule` говорит, пора ли прогон или дайджест; между делами `serve.Service` ждёт команды бота long polling и передаёт их в `notify/bot.py`.

**Tech Stack:** Python 3.12, uv, httpx (Telegram Bot API напрямую), pydantic v2, PyYAML, sqlite3, zoneinfo + `tzdata` (новая зависимость), pytest. PowerShell-скрипт для Планировщика Windows.

**Spec:** `docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md` (этап 3 из §11; Telegram §7.1; расписание §4; таблицы `notifications`, `user_marks` §5; надёжность §8; тесты §9). Уточнения этого плана записываются в спеку новым §13 (задача 11).

**Предыдущие этапы:** этапы 1 и 2 в `main` (277 офлайн-тестов). Отложенные замечания: `docs/superpowers/notes/2026-10-29-stage1-followups.md` (раздел «Этап 3») и `docs/superpowers/notes/2026-10-30-stage2-followups.md`.

## Решения, принятые при подготовке плана

Решения 3 и 4 выбрал пользователь, остальные приняты при планировании. Они обязательны для исполнителей.

1. **Telegram без библиотеки.** Bot API вызывается напрямую через httpx: `sendMessage`, `getUpdates`, `getMe`. `python-telegram-bot` из спеки §10 не используется: он асинхронный, а весь проект синхронный и работает с одним соединением SQLite. Тесты, как и у остальных сервисов, идут через `httpx.MockTransport`. Фото объявления показывается превью ссылки над текстом (`link_preview_options`). Так одно сообщение вмещает до 4096 символов, а если Telegram не загрузит картинку, сообщение всё равно уйдёт.
2. **Расписание без APScheduler.** `serve` работает в одном потоке:
   - сначала делает просроченное (прогон, дайджест);
   - затем ждёт команды бота через `getUpdates` с таймаутом до следующего дела, но не больше 50 с;
   - во время прогона (1–2 мин) команды ждут и выполняются после него.
3. **Сон и выключенный ПК** (выбор пользователя: «догнать после пробуждения»).
   - Пропущенные прогоны схлопываются в один догоняющий. Его не делают, если до следующего прогона по расписанию меньше 60 минут (`schedule.catch_up_gap_minutes`).
   - Прогон, опоздавший не больше чем на 5 минут, считается вовремя.
   - Дайджест, пропущенный в 20:00, отправляется до конца того же дня по Кишинёву.
4. **Автозапуск** (выбор пользователя). `scripts/autostart.ps1 -Install` регистрирует задачу Планировщика «Homealize»:
   - запуск при входе пользователя;
   - команда `.venv\Scripts\pythonw.exe -m homealize.cli serve`, без окна;
   - без ограничения времени работы.

   `-Remove` останавливает и удаляет задачу. Скрипт запускает пользователь сам; исполнители его только проверяют без параметров.
5. **Очередь уведомлений.**
   - Подготовленные сообщения пишутся в `notifications`, а `outbox.deliver` их отправляет.
   - `key` уникален, поэтому повторный прогон не дублирует сообщение.
   - Неотправленное остаётся `pending` и уходит при следующей отправке.
   - После 3 неудачных попыток, при постоянной ошибке (HTTP 400) или через 24 часа сообщение получает статус `dropped`.
6. **Окно «новых».** Уведомления после прогона готовятся по окну. Окно начинается с первого прогона после последнего, по которому уведомления уже готовились (`runs.notified_at`), но не раньше чем сутки назад.
   - Прогон с упавшей оценкой, `--dry-run` и прогон без настроенного Telegram окно не закрывают: их новые объявления придут со следующим нормальным прогоном.
   - У пользователя `notified_at` пока нет ни у одного прогона, поэтому первый прогон serve пришлёт подходящие объявления, найденные за последние сутки (не больше 10 и строку «ещё N»).
7. **«Новый участок»** (спека §7.1) — объявление, которое отвечает всем условиям:
   - впервые увидено в окне;
   - в рейтинге (`scores.rank` не NULL: прошло все фильтры, в бюджете, не подозрительное, не дубликат, не скрыто);
   - `total ≥ 70` или `rank ≤ 10`;
   - у его группы дубликатов нет активных участников, увиденных раньше окна.

   Отправляется не больше 10 за прогон, по порядку мест, а сверх этого — одно сообщение «…и ещё N — /new».
8. **Первый прогон.** Если в базе нет объявлений, увиденных раньше начала окна, вместо «новых» и снижений цен отправляется одна сводка «загружено N, в рейтинге M, топ-10».
9. **Снижение цены.** Последняя запись `price_history` сделана в окне, а предыдущая цена выше хотя бы на 5%.
   - В одной валюте сравниваются исходные цены, в разных — цены в евро по последнему курсу BNM из кэша.
   - Объявление должно быть в рейтинге, `total ≥ 60`.
10. **Скрытые и избранные** (`user_marks`).
    - `/hide` даёт `scores.excluded_reason = 'hidden'`. Движок оценки ставит эту причину, только если других нет. `/hide` и `/unhide` сразу пересчитывают места в снимке `scores` без обращения к сервисам.
    - ★ видна в сообщениях бота. Кроме `/fav` из спеки добавлены `/unfav` и `/favs`.
11. **Технические тревоги.**
    - Виды:
      - `rate_limited`;
      - `empty_search`;
      - `source_unavailable`;
      - `source_schema`;
      - `unparsed` (больше 20% не разобрано);
      - `too_few_seen` (обход увидел меньше половины известных объявлений);
      - `crashed`;
      - `evaluation_failed`;
      - `export_failed`.
    - Тревога одного вида приходит не чаще раза в 6 часов.
    - Предупреждения Overpass, OSRM и BNM тревогой не считаются (оценка продолжается на кэше): их видно в `/status`.
12. **Блокировки.**
    - На время прогона файл `data/run.lock` блокируется средствами ОС (`msvcrt` в Windows, `fcntl` в Linux). `rescore` берёт ту же блокировку.
    - `serve` держит `data/serve.lock` всё время работы.
    - Второй прогон или второй serve получают код выхода 4.
    - Под блокировкой прогоны в статусе `running` считаются зависшими и закрываются как `failed`.
13. **Секреты.** `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` берутся из `.env` в корне проекта; переменные окружения важнее файла. Токен никогда не попадает в логи, базу, тексты исключений и вывод.
14. **Коды выхода CLI:**
    - `0` — всё выполнено;
    - `1` — обход прерван;
    - `2` — настройки не прочитаны (в том числе `.env`, справочник сёл, отсутствие Telegram для `serve`);
    - `3` — оценка, Excel, обновление данных или Telegram не удались;
    - `4` — уже идёт другой прогон или serve уже запущен.
15. **`run` без Telegram** работает как раньше и пишет «Telegram не настроен». `run --dry-run` и `digest --dry-run` печатают сообщения в консоль вместо отправки, при этом оценка и Excel обновляются.
16. **Отложенные пункты этапа 1, закрываемые здесь:**
    - блокировка от двух прогонов;
    - уборка зависших прогонов;
    - настоящее `finished_at`;
    - `now` в UTC внутри `run_once`;
    - `mark_unseen` считает снятыми только бывшие активные;
    - HTTP-клиент: точный маркер лимита, все `httpx.RequestError`, 401/403 — «недоступно», `from exc`, контекстные менеджеры;
    - ошибки GraphQL делятся на «схема изменилась» и «временный сбой»;
    - пустая последняя страница при уменьшившемся `count` больше не валит прогон;
    - соединение CLI закрывается, даже если не удалось создать источник;
    - инструкция для сужения фильтра площади.

    Из этапа 2 закрываются:
    - короткий текст ошибок в `/status` и тревогах;
    - проверка `settlements.yaml` при старте.

## Global Constraints

- Python `>=3.12`, запуск только через `uv`. Новая зависимость одна — `tzdata` (база часовых поясов для `zoneinfo` на Windows).
- Запросы к 999.md не меняются: по одному, с паузой 2–3 с. `feature(id: 16)` и `owner` не запрашиваются.
- **Telegram:**
  - только Bot API `https://api.telegram.org/bot<token>/<method>` и методы `sendMessage`, `getUpdates`, `getMe`;
  - `parse_mode = HTML`, все значения из базы экранируются `html.escape`;
  - текст длиннее 4000 символов делится по строкам;
  - пауза между уведомлениями 1 с.
- Токен бота никогда не пишется в логи, базу, исключения и вывод; ошибки httpx наружу не пробрасываются (`from None`).
- Бот отвечает только на сообщения из `TELEGRAM_CHAT_ID`. Сообщения из других чатов игнорируются, в лог пишется только id чата.
- **Тесты никогда не обращаются к настоящему Telegram:**
  - `tests/conftest.py` убирает `TELEGRAM_*` из окружения;
  - CLI-тесты указывают `telegram.env_file` во временную папку;
  - клиент в тестах — `FakeTelegram` или `httpx.MockTransport`.
- В сообщениях только то, что уже хранится в базе (телефонов и имён продавцов там нет).
- **Расписание (Europe/Chisinau):**
  - прогоны в 07:00, 10:00, 13:00, 16:00, 19:00, 22:00;
  - дайджест в 20:00;
  - догоняющий прогон не делается, если до следующего меньше 60 мин;
  - всё задаётся в `config/settings.yaml`.
- **Пороги (в `settings.yaml`, секция `telegram`):**
  - новый участок: `total ≥ 70` или `rank ≤ 10`, не больше 10 за прогон;
  - снижение цены: хотя бы на 5% и `total ≥ 60`;
  - повтор тревоги одного вида — не чаще раза в 6 ч;
  - дайджест — топ-10.
- Тексты для пользователя — на русском. Время в сообщениях — по Кишинёву, в базе — ISO 8601 в UTC.
- Тесты не ходят в сеть, кроме `pytest -m live`; время и сон в тестах подменяются.
- Функции `storage/*` и планировщики уведомлений не делают commit. Исключения: `outbox.deliver` фиксирует каждое отправленное сообщение сразу, а команды бота фиксируют свои отметки.

## Структура файлов

```
.env.example                                НОВЫЙ: шаблон TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
.gitignore                                  + data/*.lock
pyproject.toml, uv.lock                     + tzdata
config/settings.yaml                        + storage.*_lock_path, schedule, telegram
scripts/autostart.ps1                       НОВЫЙ: задача Планировщика Windows (UTF-8 с BOM)
src/homealize/config.py                     + ScheduleSettings, TelegramSettings, parse_hhmm,
                                              TelegramSecrets, read_env_file, load_telegram_secrets
src/homealize/models.py                     + RunSummary.failure, failure_detail
src/homealize/pipeline.py                   now в UTC, clock для finished_at, вид сбоя
src/homealize/runlock.py                    НОВЫЙ: блокировка файлом (LockBusyError, hold_lock)
src/homealize/storage/repo.py               + close_stale_runs, has_listings_before,
                                              notify_window_start, mark_notified; mark_unseen
src/homealize/sources/nine99/http.py        маркер лимита, RequestError, 401/403, контекстный менеджер
src/homealize/sources/nine99/parse.py       ошибки GraphQL: схема или временный сбой
src/homealize/sources/nine99/source.py      конец прохода при уменьшившемся count; контекстный менеджер
src/homealize/storage/migrations/003_notifications.sql   НОВЫЙ: notifications, user_marks, runs.notified_at
src/homealize/storage/marks.py              НОВЫЙ: ★ и скрытые
src/homealize/storage/scores.py             + assign_ranks, apply_hidden, get_scored; images в выборке
src/homealize/scoring/components.py         + EXCLUSION_LABELS["hidden"]
src/homealize/scoring/engine.py             скрытые → excluded_reason, места через assign_ranks
src/homealize/notify/__init__.py            НОВЫЙ
src/homealize/notify/telegram.py            НОВЫЙ: клиент Bot API
src/homealize/notify/outbox.py              НОВЫЙ: очередь и доставка
src/homealize/notify/format.py              НОВЫЙ: тексты сообщений
src/homealize/notify/events.py              НОВЫЙ: новые участки, снижения цен, первый прогон
src/homealize/notify/alerts.py              НОВЫЙ: тех. тревоги
src/homealize/notify/digest.py              НОВЫЙ: дайджест
src/homealize/notify/bot.py                 НОВЫЙ: команды бота
src/homealize/jobs.py                       НОВЫЙ: цикл прогона, дайджест
src/homealize/scheduler.py                  НОВЫЙ: когда пора
src/homealize/serve.py                      НОВЫЙ: цикл serve
src/homealize/cli.py                        + run --dry-run, serve, digest, telegram-test; код 4
src/homealize/logging_setup.py              без консоли под pythonw
tests/conftest.py                           + очистка TELEGRAM_* из окружения
tests/factories.py                          + score_record, FakeTelegram
tests/...                                   тесты к каждой задаче
README.md, спека §13
```

## Порядок задач

1. Настройки этапа 3, секреты Telegram, `tzdata`.
2. Надёжный прогон: вид сбоя, время окончания, зависшие прогоны, блокировка.
3. Клиент 999.md: точнее различать сбои.
4. Миграция 003, ★ и скрытые, места в снимке оценки.
5. Клиент Telegram и очередь уведомлений.
6. Тексты сообщений, новые участки, снижения цен, первый прогон.
7. Технические тревоги и дайджест.
8. Команды бота.
9. Цикл прогона; CLI `run --dry-run`, `digest`, `telegram-test`; интеграционный тест.
10. Расписание и `serve`.
11. Автозапуск, README, спека §13, живая проверка.

---

### Task 1: Настройки этапа 3, секреты Telegram, `tzdata`

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (через `uv add tzdata`)
- Modify: `src/homealize/config.py`
- Modify: `config/settings.yaml`
- Create: `.env.example`
- Modify: `.gitignore`
- Modify: `tests/conftest.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: —
- Produces (всё в `homealize.config`):
  - `StorageSettings.run_lock_path: Path = Path("data/run.lock")`, `StorageSettings.serve_lock_path: Path = Path("data/serve.lock")`;
  - `ScheduleSettings`:
    - поля `timezone: str`, `run_times: list[str]` (отсортированы, «ЧЧ:ММ»), `digest_time: str`, `catch_up_gap_minutes: int`;
    - свойство `zone -> ZoneInfo`;
  - `parse_hhmm(value: str) -> datetime.time`;
  - `TelegramSettings` с полями:
    - `env_file`, `api_url`, `timeout_seconds`, `poll_timeout_seconds`, `send_pause_seconds`;
    - `new_min_total`, `new_max_rank`, `max_new_per_run`, `price_drop_min_share`, `price_drop_min_total`;
    - `alert_repeat_hours`, `digest_top`, `top_default`, `top_max`, `max_attempts`, `expire_hours`;
  - `Settings.schedule`, `Settings.telegram`;
  - `TOKEN_VAR = "TELEGRAM_BOT_TOKEN"`, `CHAT_VAR = "TELEGRAM_CHAT_ID"`;
  - `TelegramSecrets(token: str, chat_id: int | None)` — frozen dataclass, токен не виден в `repr`;
  - `read_env_file(path: Path) -> dict[str, str]`;
  - `load_telegram_secrets(path: Path, environ: Mapping[str, str] | None = None) -> TelegramSecrets | None` — `None`, если токена нет; `ValueError`, если `TELEGRAM_CHAT_ID` не число или строка `.env` без `=`.

- [ ] **Step 1: Добавить зависимость**

Run: `uv add tzdata`
Expected: в `pyproject.toml` появилась строка `"tzdata>=…"` в `dependencies`, `uv.lock` обновлён.

- [ ] **Step 2: Написать падающие тесты**

Добавить в конец `tests/test_config.py` (импорты — в начало файла, к существующим):

```python
from datetime import time

from homealize.config import load_telegram_secrets, parse_hhmm


def test_stage3_defaults_match_spec():
    s = Settings()
    assert s.schedule.run_times == ["07:00", "10:00", "13:00", "16:00", "19:00", "22:00"]
    assert (s.schedule.digest_time, s.schedule.timezone, s.schedule.catch_up_gap_minutes) == (
        "20:00", "Europe/Chisinau", 60,
    )
    assert s.schedule.zone.key == "Europe/Chisinau"
    t = s.telegram
    assert (t.new_min_total, t.new_max_rank, t.max_new_per_run) == (70, 10, 10)
    assert (t.price_drop_min_share, t.price_drop_min_total) == (0.05, 60)
    assert (t.alert_repeat_hours, t.digest_top, t.top_default, t.top_max) == (6, 10, 10, 30)
    assert s.storage.run_lock_path == Path("data/run.lock")
    assert s.storage.serve_lock_path == Path("data/serve.lock")


def test_parse_hhmm():
    assert parse_hhmm("07:05") == time(7, 5)


@pytest.mark.parametrize("value", ["7:00", "24:00", "07:60", "seven"])
def test_bad_run_time_is_rejected(value):
    with pytest.raises(ValidationError, match="ЧЧ:ММ"):
        Settings.model_validate({"schedule": {"run_times": [value]}})


def test_bad_digest_time_is_rejected():
    with pytest.raises(ValidationError, match="ЧЧ:ММ"):
        Settings.model_validate({"schedule": {"digest_time": "20-00"}})


def test_run_times_are_sorted_and_unique():
    s = Settings.model_validate({"schedule": {"run_times": ["13:00", "07:00"]}})
    assert s.schedule.run_times == ["07:00", "13:00"]
    with pytest.raises(ValidationError, match="повторяется"):
        Settings.model_validate({"schedule": {"run_times": ["07:00", "07:00"]}})
    with pytest.raises(ValidationError, match="хотя бы одно"):
        Settings.model_validate({"schedule": {"run_times": []}})


def test_unknown_timezone_is_rejected():
    with pytest.raises(ValidationError, match="часовой пояс"):
        Settings.model_validate({"schedule": {"timezone": "Mars/Olympus"}})


def test_top_default_cannot_exceed_top_max():
    with pytest.raises(ValidationError):
        Settings.model_validate({"telegram": {"top_default": 40, "top_max": 30}})


def test_env_file_is_read(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# комментарий\nTELEGRAM_BOT_TOKEN='123:abc'\nexport TELEGRAM_CHAT_ID=42\n\n", encoding="utf-8"
    )
    secrets = load_telegram_secrets(env, environ={})
    assert (secrets.token, secrets.chat_id) == ("123:abc", 42)
    assert "123:abc" not in repr(secrets)


def test_env_file_with_bom(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=t\n", encoding="utf-8-sig")
    assert load_telegram_secrets(env, environ={}).token == "t"


def test_environment_overrides_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=file\nTELEGRAM_CHAT_ID=1\n", encoding="utf-8")
    secrets = load_telegram_secrets(env, environ={"TELEGRAM_BOT_TOKEN": "env", "TELEGRAM_CHAT_ID": "2"})
    assert (secrets.token, secrets.chat_id) == ("env", 2)


def test_missing_token_means_telegram_is_off(tmp_path):
    assert load_telegram_secrets(tmp_path / "нет.env", environ={}) is None
    empty = tmp_path / ".env"
    empty.write_text("TELEGRAM_BOT_TOKEN=\n", encoding="utf-8")
    assert load_telegram_secrets(empty, environ={}) is None


def test_token_without_chat_id(tmp_path):
    secrets = load_telegram_secrets(tmp_path / "нет.env", environ={"TELEGRAM_BOT_TOKEN": "t"})
    assert (secrets.token, secrets.chat_id) == ("t", None)


def test_bad_chat_id_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="числом"):
        load_telegram_secrets(tmp_path / "нет.env", environ={"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "abc"})


def test_bad_env_line_is_an_error_without_its_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN 123:secret\n", encoding="utf-8")
    with pytest.raises(ValueError, match="строка 1") as caught:
        load_telegram_secrets(env, environ={})
    assert "secret" not in str(caught.value)
```

- [ ] **Step 3: Запустить — тесты падают**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL — `ImportError: cannot import name 'load_telegram_secrets'`.

- [ ] **Step 4: Реализовать настройки**

В `src/homealize/config.py`:

1. Импорты в начале файла станут такими:

```python
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
```

2. После `DEFAULT_UTILITY_WEIGHTS` добавить:

```python
DEFAULT_RUN_TIMES = ["07:00", "10:00", "13:00", "16:00", "19:00", "22:00"]
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
TOKEN_VAR = "TELEGRAM_BOT_TOKEN"
CHAT_VAR = "TELEGRAM_CHAT_ID"


def parse_hhmm(value: str) -> time:
    """«07:05» → time(7, 5); формат уже проверен ScheduleSettings."""
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _check_hhmm(field_name: str, value: str) -> None:
    if not _HHMM.match(value):
        raise ValueError(f"{field_name}: «{value}» — нужно время ЧЧ:ММ, например 07:00")
```

3. `StorageSettings` заменить на:

```python
class StorageSettings(_Section):
    db_path: Path = Path("data/homealize.db")
    run_lock_path: Path = Path("data/run.lock")  # пока идёт прогон, второй не начнётся
    serve_lock_path: Path = Path("data/serve.lock")  # второй serve не запустится
```

4. После `ExportSettings` добавить:

```python
class ScheduleSettings(_Section):
    timezone: str = "Europe/Chisinau"
    run_times: list[str] = Field(default_factory=lambda: list(DEFAULT_RUN_TIMES))
    digest_time: str = "20:00"
    catch_up_gap_minutes: int = Field(60, ge=0)

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"timezone: неизвестный часовой пояс «{value}»") from exc
        return value

    @field_validator("run_times")
    @classmethod
    def _check_run_times(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("run_times: нужно хотя бы одно время прогона")
        for value in values:
            _check_hhmm("run_times", value)
        if len(set(values)) != len(values):
            raise ValueError("run_times: время повторяется")
        return sorted(values)

    @field_validator("digest_time")
    @classmethod
    def _check_digest_time(cls, value: str) -> str:
        _check_hhmm("digest_time", value)
        return value

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class TelegramSettings(_Section):
    env_file: Path = Path(".env")  # TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID
    api_url: str = "https://api.telegram.org"
    timeout_seconds: float = Field(30.0, gt=0)
    poll_timeout_seconds: int = Field(50, ge=1, le=50)
    send_pause_seconds: float = Field(1.0, ge=0)
    new_min_total: float = Field(70.0, ge=0, le=100)
    new_max_rank: int = Field(10, ge=0)
    max_new_per_run: int = Field(10, ge=1)
    price_drop_min_share: float = Field(0.05, gt=0, lt=1)
    price_drop_min_total: float = Field(60.0, ge=0, le=100)
    alert_repeat_hours: float = Field(6.0, ge=0)
    digest_top: int = Field(10, ge=1, le=30)
    top_default: int = Field(10, ge=1)
    top_max: int = Field(30, ge=1)
    max_attempts: int = Field(3, ge=1)
    expire_hours: float = Field(24.0, gt=0)

    @model_validator(mode="after")
    def _check_top(self) -> "TelegramSettings":
        if self.top_default > self.top_max:
            raise ValueError("top_default не может быть больше top_max")
        return self
```

5. В `Settings` после `export` добавить два поля:

```python
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
```

6. В конец файла (после `load_settings`) добавить:

```python
@dataclass(frozen=True)
class TelegramSecrets:
    token: str = field(repr=False)
    chat_id: int | None  # None: бот создан, но чат ещё не известен (см. `homealize telegram-test`)


def read_env_file(path: Path) -> dict[str, str]:
    """Строки ИМЯ=значение; пустые строки и # комментарии пропускаются, кавычки вокруг значения снимаются."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"{path}, строка {number}: нужно ИМЯ=значение")  # само значение не показываем
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_telegram_secrets(path: Path, environ: Mapping[str, str] | None = None) -> TelegramSecrets | None:
    """Токен и чат из окружения или .env (окружение важнее); None — Telegram не настроен."""
    environ = os.environ if environ is None else environ
    file_values = read_env_file(path)
    token = (environ.get(TOKEN_VAR) or file_values.get(TOKEN_VAR) or "").strip()
    if not token:
        return None
    chat = (environ.get(CHAT_VAR) or file_values.get(CHAT_VAR) or "").strip()
    if not chat:
        return TelegramSecrets(token, None)
    try:
        return TelegramSecrets(token, int(chat))
    except ValueError:
        raise ValueError(f"{CHAT_VAR} должен быть числом, сейчас «{chat}»") from None
```

- [ ] **Step 5: Настройки проекта, шаблон `.env`, `.gitignore`, conftest**

В `config/settings.yaml` секцию `storage` заменить на:

```yaml
storage:
  db_path: "data/homealize.db"
  run_lock_path: "data/run.lock"     # пока идёт прогон, второй не начнётся
  serve_lock_path: "data/serve.lock" # второй serve не запустится
```

и в конец файла добавить:

```yaml

schedule:
  timezone: "Europe/Chisinau"
  run_times: ["07:00", "10:00", "13:00", "16:00", "19:00", "22:00"]
  digest_time: "20:00"
  catch_up_gap_minutes: 60       # пропущенный прогон не догоняется, если до следующего меньше стольких минут

telegram:
  env_file: ".env"               # здесь TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID (см. .env.example)
  new_min_total: 70              # «новый участок»: балл не ниже этого…
  new_max_rank: 10               # …или место в рейтинге не ниже этого
  max_new_per_run: 10            # больше за прогон не присылать, остальные — одной строкой
  price_drop_min_share: 0.05     # «снижение цены»: хотя бы на эту долю…
  price_drop_min_total: 60       # …и балл после снижения не ниже этого
  alert_repeat_hours: 6          # одинаковая тех. тревога — не чаще
  digest_top: 10                 # сколько мест показывать в дайджесте
  top_default: 10                # /top без числа
  top_max: 30                    # /top N: не больше
```

Создать `.env.example`:

```
# Скопируйте этот файл в .env и заполните. Файл .env в git не попадает.
# Токен выдаёт @BotFather в Telegram после команды /newbot.
TELEGRAM_BOT_TOKEN=
# Ваш chat id: напишите боту любое сообщение и выполните `uv run homealize telegram-test`.
TELEGRAM_CHAT_ID=
```

В `.gitignore` после строки `data/*.db-shm` добавить строку `data/*.lock`.

В `tests/conftest.py` добавить фикстуру (рядом с `db`):

```python
@pytest.fixture(autouse=True)
def _no_real_telegram(monkeypatch):
    """Тесты никогда не берут настоящий токен бота из окружения."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
```

- [ ] **Step 6: Запустить тесты**

Run: `uv run pytest tests/test_config.py -q`
Expected: PASS, в том числе `test_project_settings_file_matches_defaults` (значения в `settings.yaml` совпадают со значениями по умолчанию).

Run: `uv run pytest -q`
Expected: PASS (все прежние тесты).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/homealize/config.py config/settings.yaml .env.example .gitignore tests/conftest.py tests/test_config.py
git commit -m "feat: stage 3 settings - schedule, Telegram thresholds, .env secrets"
```

---

### Task 2: Надёжный прогон: вид сбоя, время окончания, зависшие прогоны, блокировка

**Files:**
- Modify: `src/homealize/models.py` (`RunSummary`)
- Modify: `src/homealize/pipeline.py` (заменить целиком)
- Modify: `src/homealize/storage/repo.py` (`mark_unseen`, новая `close_stale_runs`)
- Create: `src/homealize/runlock.py`
- Test: `tests/test_pipeline.py`, `tests/storage/test_repo.py`, `tests/test_runlock.py` (новый)

**Interfaces:**
- Consumes: исключения `homealize.sources.base` (`SourceError`, `RateLimitedError`, `EmptySearchError`, `SourceSchemaError`).
- Produces:
  - `RunSummary.failure: str | None` — вид сбоя: `rate_limited`, `empty_search`, `source_schema`, `source_unavailable`, `unparsed`, `too_few_seen`, `crashed` или `None`;
  - `RunSummary.failure_detail: str` — текст первой причины, `""` без сбоя;
  - `run_once(conn, source, settings, *, now, clock=None) -> RunSummary`:
    - `now` приводится к UTC и становится `started_at` и `first_seen_at`;
    - `clock()` даёт `finished_at` (по умолчанию `datetime.now(UTC)`);
  - `pipeline.failure_kind(exc: SourceError) -> str`;
  - `repo.close_stale_runs(conn) -> int`, `repo.STALE_RUN_MESSAGE`;
  - `repo.mark_unseen(...)` по-прежнему возвращает `int`, но теперь это число снятых **активных** объявлений;
  - `homealize.runlock`:
    - `LockBusyError(Exception)`;
    - `hold_lock(path: Path)` — контекстный менеджер; при занятой блокировке бросает `LockBusyError`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/test_pipeline.py` (импорты — к существующим в начале файла):

```python
from datetime import timezone

from homealize.sources.base import EmptySearchError, RateLimitedError, SourceSchemaError, SourceUnavailableError


def test_finished_at_comes_from_the_clock(db):
    run_once(db, FakeSource([page(make_listing(id="a"))]), SETTINGS, now=T0, clock=lambda: at(1))
    run = repo.last_run(db)
    assert (run["started_at"], run["finished_at"]) == (T0.isoformat(), at(1).isoformat())


def test_now_is_stored_in_utc(db):
    local = T0.astimezone(timezone(timedelta(hours=2)))
    run_once(db, FakeSource([page(make_listing(id="a"))]), SETTINGS, now=local, clock=lambda: local)
    assert repo.get_listing(db, "a")["first_seen_at"] == T0.isoformat()
    run = repo.last_run(db)
    assert (run["started_at"], run["finished_at"]) == (T0.isoformat(), T0.isoformat())


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (RateLimitedError("лимит"), "rate_limited"),
        (EmptySearchError("0 объявлений"), "empty_search"),
        (SourceSchemaError("схема"), "source_schema"),
        (SourceUnavailableError("сеть"), "source_unavailable"),
    ],
)
def test_interrupted_scan_reports_failure_kind(db, error, kind):
    summary = run_once(db, FakeSource([], fail_at=0, error=error), SETTINGS, now=T0)
    assert summary.failure == kind
    assert summary.failure_detail == f"{type(error).__name__}: {error}"


def test_complete_scan_has_no_failure(db):
    summary = run_once(db, FakeSource([page(make_listing(id="a"))]), SETTINGS, now=T0)
    assert (summary.failure, summary.failure_detail) == (None, "")


def test_too_many_unparsed_is_failure_unparsed(db):
    source = FakeSource([page(make_listing(id="a"), errors=("e1", "e2"), unparsed=("x", "y"))])
    summary = run_once(db, source, SETTINGS, now=T0)
    assert summary.failure == "unparsed"
    assert "нераспознанных" in summary.failure_detail


def test_seeing_too_few_known_listings_is_failure_too_few_seen(db):
    run_once(db, FakeSource([page(*[make_listing(id=str(i)) for i in range(4)])]), SETTINGS, now=T0)
    summary = run_once(db, FakeSource([page(make_listing(id="0"))]), SETTINGS, now=at(3))
    assert summary.failure == "too_few_seen"


def test_crash_is_recorded_as_failed_and_reraised(db):
    with pytest.raises(RuntimeError):
        run_once(db, FakeSource([], fail_at=0, error=RuntimeError("сломалось")), SETTINGS, now=T0)
    run = repo.last_run(db)
    assert run["status"] == "failed"
    assert json.loads(run["errors"]) == ["RuntimeError: сломалось"]
```

Добавить в `tests/storage/test_repo.py`:

```python
def test_close_stale_runs_marks_running_as_failed(db):
    stale = repo.start_run(db, T0)
    done = repo.start_run(db, T0)
    repo.finish_run(db, done, RunSummary(search_complete=True), now=T1)
    assert repo.close_stale_runs(db) == 1
    row = db.execute("SELECT * FROM runs WHERE id = ?", (stale,)).fetchone()
    assert row["status"] == "failed"
    assert row["finished_at"] is None
    assert json.loads(row["errors"]) == [repo.STALE_RUN_MESSAGE]
    assert db.execute("SELECT status FROM runs WHERE id = ?", (done,)).fetchone()[0] == "ok"
    assert repo.close_stale_runs(db) == 0


def test_mark_unseen_counts_only_active_listings(db):
    save(db, make_listing(id="a"))
    save(db, make_listing(id="f"), status="filtered_out", reason="region")
    assert repo.mark_unseen(db, set(), threshold=1) == 1
    assert repo.get_listing(db, "a")["status"] == "removed"
    assert repo.get_listing(db, "f")["status"] == "removed"
```

Создать `tests/test_runlock.py`:

```python
import pytest

from homealize.runlock import LockBusyError, hold_lock


def test_second_holder_is_refused_until_the_first_releases(tmp_path):
    path = tmp_path / "data" / "run.lock"
    with hold_lock(path):
        assert path.exists()
        with pytest.raises(LockBusyError):
            with hold_lock(path):
                pass
    with hold_lock(path):
        pass


def test_lock_is_released_when_the_body_fails(tmp_path):
    path = tmp_path / "run.lock"
    with pytest.raises(RuntimeError):
        with hold_lock(path):
            raise RuntimeError("сбой")
    with hold_lock(path):
        pass


def test_different_files_do_not_block_each_other(tmp_path):
    with hold_lock(tmp_path / "run.lock"):
        with hold_lock(tmp_path / "serve.lock"):
            pass
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `uv run pytest tests/test_pipeline.py tests/storage/test_repo.py tests/test_runlock.py -q`
Expected: FAIL — `run_once() got an unexpected keyword argument 'clock'`, нет `close_stale_runs`, нет модуля `homealize.runlock`.

- [ ] **Step 3: `RunSummary` и `repo`**

В `src/homealize/models.py` в `RunSummary` после `errors` добавить:

```python
    failure: str | None = None  # вид сбоя для тех. тревоги (notify/alerts.py); None — обход прошёл
    failure_detail: str = ""  # текст первой причины сбоя
```

В `src/homealize/storage/repo.py`:

1. После класса `UpsertOutcome` добавить константу:

```python
STALE_RUN_MESSAGE = "Прогон прерван: процесс остановился раньше, чем записал итоги"
```

2. `mark_unseen` заменить на:

```python
def mark_unseen(conn: sqlite3.Connection, seen_ids: set[str], *, threshold: int) -> int:
    """После полного прохода: +1 пропуск всем, кого не видели; снимает тех, кто пропущен threshold раз.

    Возвращает, сколько снято активных объявлений (бывшие filtered_out тоже снимаются, но не считаются).
    """
    rows = conn.execute("SELECT id, missed_scans, status FROM listings WHERE status != 'removed'").fetchall()
    removed = 0
    for row in rows:
        if row["id"] in seen_ids:
            continue
        missed = row["missed_scans"] + 1
        if missed >= threshold:
            conn.execute("UPDATE listings SET missed_scans = ?, status = 'removed' WHERE id = ?", (missed, row["id"]))
            removed += row["status"] == "active"
        else:
            conn.execute("UPDATE listings SET missed_scans = ? WHERE id = ?", (missed, row["id"]))
    return removed
```

3. После `add_run_errors` добавить:

```python
def close_stale_runs(conn: sqlite3.Connection) -> int:
    """Прогоны, оставшиеся в статусе running (Ctrl+C, сбой записи итогов), помечаются failed.

    Вызывать только под блокировкой прогона (homealize.runlock): тогда ни один из них уже не идёт.
    """
    rows = conn.execute("SELECT id FROM runs WHERE status = 'running'").fetchall()
    for row in rows:
        conn.execute("UPDATE runs SET status = 'failed' WHERE id = ?", (row["id"],))
        add_run_errors(conn, row["id"], [STALE_RUN_MESSAGE])
    return len(rows)
```

- [ ] **Step 4: `pipeline.py`**

Заменить `src/homealize/pipeline.py` целиком:

```python
"""Один прогон: обход источника, фильтры, сохранение, история цен, снятие пропавших."""

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from homealize.config import Settings
from homealize.filters import filter_reason
from homealize.models import RunSummary
from homealize.sources.base import (
    EmptySearchError,
    RateLimitedError,
    Source,
    SourceError,
    SourceSchemaError,
)
from homealize.storage import repo
from homealize.storage.repo import UpsertOutcome

log = logging.getLogger(__name__)

MAX_UNPARSED_SHARE = 0.2  # больше этой доли нераспознанных объявлений: обход не считается надёжным
MIN_SEEN_SHARE = 0.5  # обход, увидевший меньше этой доли известных объявлений, ничего не снимает


def run_once(
    conn: sqlite3.Connection,
    source: Source,
    settings: Settings,
    *,
    now: datetime,
    clock: Callable[[], datetime] | None = None,
) -> RunSummary:
    """now — начало прогона (оно же «впервые увидено»); clock() даёт время окончания."""
    now = now.astimezone(UTC)
    clock = clock or (lambda: datetime.now(UTC))
    summary = RunSummary()
    run_id = repo.start_run(conn, now)
    conn.commit()
    seen: set[str] = set()
    unparsed_ids: set[str] = set()  # нераспознанные объявления: не сохраняем, но и пропавшими не считаем
    unparsed = received = 0
    try:
        for page in source.iter_pages():
            summary.errors.extend(page.parse_errors)
            unparsed_ids.update(page.unparsed_ids)
            unparsed += len(page.parse_errors)
            received += len(page.listings) + len(page.parse_errors)
            for listing in page.listings:
                if listing.id in seen:
                    continue
                seen.add(listing.id)
                reason = filter_reason(listing, settings.filters)
                outcome = repo.upsert_listing(
                    conn,
                    listing,
                    status="filtered_out" if reason else "active",
                    filter_reason=reason,
                    now=now,
                )
                _count(summary, outcome)
            conn.commit()
        problems = _removal_problems(conn, seen | unparsed_ids, unparsed=unparsed, received=received)
        if problems:
            for _kind, message in problems:
                log.error(message)
                summary.errors.append(message)
            summary.failure, summary.failure_detail = problems[0]
        else:
            summary.removed = repo.mark_unseen(
                conn, seen | unparsed_ids, threshold=settings.lifecycle.removal_missed_scans
            )
            summary.search_complete = True
    except SourceError as exc:
        conn.rollback()
        log.error("Обход %s прерван: %s", source.name, exc)
        message = f"{type(exc).__name__}: {exc}"
        summary.errors.append(message)
        summary.failure, summary.failure_detail = failure_kind(exc), message
    except Exception as exc:
        conn.rollback()
        log.exception("Прогон %s упал", run_id)
        message = f"{type(exc).__name__}: {exc}"
        summary.errors.append(message)
        summary.failure, summary.failure_detail = "crashed", message
        _finish(conn, run_id, summary, seen, clock())
        raise

    _finish(conn, run_id, summary, seen, clock())
    log.info(
        "Прогон %s завершён (%s): увидено %s, новых %s, цена изменилась %s, обновлено %s, снято %s, ошибок %s",
        run_id, summary.status, summary.ads_seen, summary.new, summary.price_changed,
        summary.changed, summary.removed, len(summary.errors),
    )
    return summary


def failure_kind(exc: SourceError) -> str:
    """Вид сбоя обхода для технической тревоги."""
    if isinstance(exc, RateLimitedError):
        return "rate_limited"
    if isinstance(exc, EmptySearchError):
        return "empty_search"
    if isinstance(exc, SourceSchemaError):
        return "source_schema"
    return "source_unavailable"


def _removal_problems(
    conn: sqlite3.Connection, visible: set[str], *, unparsed: int, received: int
) -> list[tuple[str, str]]:
    """(вид, текст) причин, по которым обходу нельзя доверять и снимать пропавшие объявления нельзя."""
    problems = []
    if received > 0 and unparsed / received > MAX_UNPARSED_SHARE:
        problems.append(
            ("unparsed", f"Слишком много нераспознанных объявлений: {unparsed} из {received} — снятие пропущено")
        )
    known = repo.count_not_removed(conn)
    if known > 0 and len(visible) < MIN_SEEN_SHARE * known:
        problems.append(
            ("too_few_seen", f"Обход увидел {len(visible)} объявлений при {known} известных — снятие пропущено")
        )
    return problems


def _count(summary: RunSummary, outcome: UpsertOutcome) -> None:
    if outcome is UpsertOutcome.NEW:
        summary.new += 1
    elif outcome is UpsertOutcome.PRICE_CHANGED:
        summary.price_changed += 1
    elif outcome is UpsertOutcome.UPDATED:
        summary.changed += 1


def _finish(
    conn: sqlite3.Connection, run_id: int, summary: RunSummary, seen: set[str], finished: datetime
) -> None:
    summary.ads_seen = len(seen)
    repo.finish_run(conn, run_id, summary, now=finished.astimezone(UTC))
    conn.commit()
```

- [ ] **Step 5: `runlock.py`**

Создать `src/homealize/runlock.py`:

```python
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
```

- [ ] **Step 6: Запустить тесты**

Run: `uv run pytest tests/test_pipeline.py tests/storage/test_repo.py tests/test_runlock.py -q`
Expected: PASS.

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/homealize/models.py src/homealize/pipeline.py src/homealize/storage/repo.py src/homealize/runlock.py tests/test_pipeline.py tests/storage/test_repo.py tests/test_runlock.py
git commit -m "fix: run failure kinds, real finish time, stale runs and a run lock"
```

---

### Task 3: Клиент 999.md: точнее различать сбои

**Files:**
- Modify: `src/homealize/sources/nine99/http.py` (заменить целиком)
- Modify: `src/homealize/sources/nine99/parse.py` (`parse_search_response`, новая `_graphql_error`)
- Modify: `src/homealize/sources/nine99/source.py` (`iter_pages`, контекстный менеджер)
- Test: `tests/nine99/test_http.py`, `tests/nine99/test_parse.py`, `tests/nine99/test_source.py`

**Interfaces:**
- Consumes: исключения `homealize.sources.base`.
- Produces:
  - `http.RATE_LIMIT_MARKER = "999.md_request_limit"`, `http.is_rate_limit_page(response) -> bool`;
  - `PoliteClient` и `Nine99Source` работают как контекстные менеджеры (`__exit__` закрывает клиент);
  - HTTP 401/403 → `SourceUnavailableError` без повторов;
  - любой `httpx.RequestError` повторяется, итоговая `SourceUnavailableError` хранит его в `__cause__`;
  - ошибки GraphQL: похожие на несовпадение схемы (`Cannot query field`, `Unknown argument`, коды `GRAPHQL_VALIDATION_FAILED`, `GRAPHQL_PARSE_FAILED`, `BAD_USER_INPUT` и т. п.) дают `SourceSchemaError`, остальные — `SourceUnavailableError` («временную»);
  - пустая страница при `0 < count <= skip` спокойно завершает проход (объявлений стало меньше ровно на границе страницы).

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/nine99/test_http.py`:

```python
def test_marker_inside_json_is_not_a_rate_limit():
    body = {"data": {"note": "999.md_request_limit"}}
    client, _, _ = make_client([httpx.Response(200, json=body)])
    assert client.post_json("/graphql", {}) == body


def test_redirect_to_the_limit_page_is_a_rate_limit():
    client, requests, _ = make_client(
        [
            httpx.Response(302, headers={"Location": "/999.md_request_limit.html"}),
            httpx.Response(200, text="<html>лимит</html>"),
        ]
    )
    with pytest.raises(RateLimitedError):
        client.post_json("/graphql", {})
    assert len(requests) == 2


@pytest.mark.parametrize("status", [401, 403])
def test_forbidden_is_unavailable_not_schema_change(status):
    client, requests, _ = make_client([httpx.Response(status, text="forbidden")])
    with pytest.raises(SourceUnavailableError, match=str(status)):
        client.post_json("/graphql", {})
    assert len(requests) == 1


def test_any_request_error_is_retried_and_kept_as_cause():
    client, requests, _ = make_client([httpx.DecodingError("битый gzip")], max_retries=1, backoff=(1.0,))
    with pytest.raises(SourceUnavailableError, match="DecodingError") as caught:
        client.post_json("/graphql", {})
    assert isinstance(caught.value.__cause__, httpx.DecodingError)
    assert len(requests) == 2


def test_client_is_a_context_manager():
    client, _, _ = make_client([httpx.Response(200, json=OK)])
    with client as same:
        assert same is client
    assert client._client.is_closed
```

Добавить в `tests/nine99/test_parse.py` (импорт `SourceUnavailableError` из `homealize.sources.base` — к существующим):

```python
def test_graphql_server_error_is_temporary():
    payload = {"errors": [{"message": "internal error", "extensions": {"code": "INTERNAL_SERVER_ERROR"}}], "data": None}
    with pytest.raises(SourceUnavailableError, match="временную"):
        parse_search_response(payload)


def test_graphql_validation_code_is_schema_error():
    payload = {"errors": [{"message": "x", "extensions": {"code": "GRAPHQL_VALIDATION_FAILED"}}]}
    with pytest.raises(SourceSchemaError, match="GraphQL"):
        parse_search_response(payload)


def test_graphql_unknown_argument_is_schema_error():
    with pytest.raises(SourceSchemaError):
        parse_search_response({"errors": [{"message": 'Unknown argument "skip" on field "searchAds"'}]})
```

В `tests/nine99/test_source.py` тест `test_empty_page_after_count_shrank_also_raises` заменить на:

```python
def test_empty_page_after_count_shrank_ends_the_scan():
    def handler(request):
        skip = json.loads(request.content)["variables"]["input"]["pagination"]["skip"]
        if skip == 0:
            return httpx.Response(200, json=load_fixture("search_page_1.json"))
        return httpx.Response(200, json={"data": {"searchAds": {"ads": [], "count": 3}}})

    pages = list(make_source(handler).iter_pages())
    assert len(pages) == 1
```

и добавить:

```python
def test_source_is_a_context_manager():
    source = make_source(fixture_handler([]))
    with source as same:
        assert same is source
    assert source._client._client.is_closed
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `uv run pytest tests/nine99 -q`
Expected: FAIL. Сейчас JSON с маркером считается лимитом, 403 — изменением схемы, `DecodingError` вылетает наружу, контекстного менеджера нет, пустая страница бросает `SourceSchemaError`, а ошибка GraphQL всегда схемная.

- [ ] **Step 3: `http.py`**

Заменить `src/homealize/sources/nine99/http.py` целиком:

```python
"""HTTP-клиент, который ходит на сайт бережно: по одному запросу, с паузами и повторами."""

import logging
import random
import time
from collections.abc import Callable

import httpx

from homealize.sources.base import RateLimitedError, SourceSchemaError, SourceUnavailableError

log = logging.getLogger(__name__)

RATE_LIMIT_MARKER = "999.md_request_limit"
RETRY_CLIENT_STATUSES = {408, 429}
FORBIDDEN_STATUSES = {401, 403}  # сайт не пускает: это недоступность, а не изменение схемы


def is_retryable_status(status: int) -> bool:
    """Повторяем 408, 429 и любой 5xx (в том числе CDN-коды вроде 522), остальное — нет."""
    return status in RETRY_CLIENT_STATUSES or status >= 500


def is_rate_limit_page(response: httpx.Response) -> bool:
    """Страница лимита: переадресация на неё или ссылка на неё в HTML. В JSON-ответе маркер не ищем."""
    if RATE_LIMIT_MARKER in str(response.url):
        return True
    if "json" in response.headers.get("content-type", ""):
        return False
    return RATE_LIMIT_MARKER in response.text[:2000]


class PoliteClient:
    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str],
        delay_seconds: tuple[float, float],
        timeout_seconds: float,
        backoff_seconds: list[float],
        max_retries: int,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=True,
        )
        self._delay = delay_seconds
        self._backoff = backoff_seconds
        self._max_retries = max_retries
        self._sleep = sleep
        self._uniform = uniform
        self._requests_made = 0

    def post_json(self, path: str, payload: dict) -> dict:
        """POST с JSON-телом; возвращает разобранный JSON-объект ответа."""
        attempt = 0
        while True:
            self._pause_between_requests()
            cause: Exception | None = None
            try:
                response = self._client.post(path, json=payload)
            except httpx.RequestError as exc:
                failure = f"{type(exc).__name__}: {exc}"
                cause = exc
            else:
                if is_rate_limit_page(response):
                    raise RateLimitedError(f"999.md ограничил частоту запросов ({response.url})")
                if response.status_code in FORBIDDEN_STATUSES:
                    raise SourceUnavailableError(f"{path}: 999.md не пускает (HTTP {response.status_code})")
                if not is_retryable_status(response.status_code):
                    return self._decode(response)
                failure = f"HTTP {response.status_code}"
            if attempt >= self._max_retries:
                raise SourceUnavailableError(f"{path}: {failure} после {attempt + 1} попыток") from cause
            wait = self._backoff[attempt]
            log.warning("%s: %s, повтор через %s с", path, failure, wait)
            self._sleep(wait)
            attempt += 1

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _pause_between_requests(self) -> None:
        if self._requests_made:
            self._sleep(self._uniform(*self._delay))
        self._requests_made += 1

    @staticmethod
    def _decode(response: httpx.Response) -> dict:
        if response.status_code != 200:
            raise SourceSchemaError(f"неожиданный ответ HTTP {response.status_code}: {response.text[:300]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise SourceSchemaError(f"ответ не является JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise SourceSchemaError("JSON-ответ не является объектом")
        return data
```

- [ ] **Step 4: `parse.py`**

В `src/homealize/sources/nine99/parse.py`:

1. Импорт исключений заменить на:

```python
from homealize.sources.base import Page, SourceError, SourceSchemaError, SourceUnavailableError
```

2. После `_PHONE_CANDIDATE` добавить:

```python
# Ошибки GraphQL, которые означают, что наш запрос больше не подходит к схеме сайта.
_SCHEMA_MESSAGE = re.compile(
    r"cannot query field|unknown argument|unknown type|unknown field|syntax error|validation|"
    r"of required type|is not defined|must have a selection|cannot be represented",
    re.IGNORECASE,
)
_SCHEMA_CODES = {"GRAPHQL_VALIDATION_FAILED", "GRAPHQL_PARSE_FAILED", "BAD_USER_INPUT"}
```

3. В `parse_search_response` первые две строки:

```python
    if payload.get("errors"):
        raise SourceSchemaError(f"GraphQL вернул ошибки: {str(payload['errors'])[:500]}")
```

заменить на:

```python
    if payload.get("errors"):
        raise _graphql_error(payload["errors"])
```

4. После `parse_search_response` добавить:

```python
def _graphql_error(errors: Any) -> SourceError:
    """Ошибки GraphQL: запрос больше не подходит к схеме сайта — или временный сбой сервера."""
    text = str(errors)[:500]
    for item in errors if isinstance(errors, list) else [errors]:
        message = str(item.get("message", "")) if isinstance(item, dict) else str(item)
        extensions = item.get("extensions") if isinstance(item, dict) else None
        code = str(extensions.get("code", "")) if isinstance(extensions, dict) else ""
        if code in _SCHEMA_CODES or _SCHEMA_MESSAGE.search(message):
            return SourceSchemaError(f"GraphQL вернул ошибки: {text}")
    return SourceUnavailableError(f"GraphQL вернул временную ошибку: {text}")
```

- [ ] **Step 5: `source.py`**

В `src/homealize/sources/nine99/source.py` в `iter_pages` блок

```python
            if received == 0:
                if skip == 0:
                    raise EmptySearchError("SearchAds вернул 0 объявлений")
                raise SourceSchemaError(f"999.md вернул пустую страницу с {skip} (найдено {page.total_count})")
```

заменить на:

```python
            if received == 0:
                if skip == 0:
                    raise EmptySearchError("SearchAds вернул 0 объявлений")
                if 0 < page.total_count <= skip:  # объявлений стало меньше ровно на границе страницы
                    log.info("999.md: объявлений стало %s — проход закончен на %s", page.total_count, skip)
                    return
                raise SourceSchemaError(f"999.md вернул пустую страницу с {skip} (найдено {page.total_count})")
```

и после метода `close` класса `Nine99Source` добавить:

```python
    def __enter__(self) -> "Nine99Source":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
```

- [ ] **Step 6: Запустить тесты**

Run: `uv run pytest tests/nine99 -q`
Expected: PASS (в том числе прежний `test_graphql_errors_raise_schema_error` — «Cannot query field» остаётся ошибкой схемы, и `test_empty_page_with_zero_count_mid_scan_raises` — при `count = 0` проход по-прежнему падает).

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/homealize/sources/nine99/http.py src/homealize/sources/nine99/parse.py src/homealize/sources/nine99/source.py tests/nine99
git commit -m "fix: tell 999.md outages from schema changes; end the scan when the count shrinks"
```

---

### Task 4: Миграция 003, ★ и скрытые, места в снимке оценки

**Files:**
- Create: `src/homealize/storage/migrations/003_notifications.sql`
- Create: `src/homealize/storage/marks.py`
- Modify: `src/homealize/storage/scores.py` (заменить целиком)
- Modify: `src/homealize/scoring/engine.py` (`rescore`, `_excluded_reason`, импорты)
- Modify: `src/homealize/scoring/components.py` (`EXCLUSION_LABELS`)
- Modify: `tests/factories.py` (+ `score_record`)
- Test: `tests/storage/test_db.py`, `tests/test_cli.py` (версия схемы), `tests/storage/test_marks.py` (новый), `tests/storage/test_scores.py` (новый), `tests/scoring/test_engine.py`

**Interfaces:**
- Consumes: `replace_scores`, `SCORE_COLUMNS` (этап 2).
- Produces:
  - Таблица `notifications`:
    - `id`, `key` UNIQUE, `kind` (`new` | `price_drop` | `first_run` | `digest` | `alert`), `topic`, `listing_id`, `payload` (JSON);
    - `status` (`pending` | `sent` | `dropped`), `attempts`, `last_error`, `created_at`, `sent_at`.
  - Таблица `user_marks`: `listing_id`, `mark` (`fav` | `hidden`), `created_at`; первичный ключ — `(listing_id, mark)`.
  - Колонка `runs.notified_at` (TEXT, NULL): по этому прогону уже готовились уведомления.
  - `homealize.storage.marks`:
    - `FAV = "fav"`, `HIDDEN = "hidden"`;
    - `add_mark(conn, listing_id, mark, *, now) -> bool`;
    - `remove_mark(conn, listing_id, mark) -> bool`;
    - `marked(conn, mark) -> set[str]`.
  - `homealize.storage.scores`:
    - `assign_ranks(conn) -> None` — места по `total DESC, price_eur, listing_id` у строк без `excluded_reason`;
    - `apply_hidden(conn, hidden: set[str]) -> None`;
    - `get_scored(conn, listing_id) -> sqlite3.Row | None`;
    - `load_scored` и `get_scored` дополнительно отдают `l.images`.
  - `EXCLUSION_LABELS["hidden"] = "скрыто вами"`; движок ставит `excluded_reason = "hidden"` последней причиной.
  - `tests.factories.score_record(listing_id, **overrides) -> dict` — строка снимка `scores` для тестов.

- [ ] **Step 1: Написать падающие тесты**

В `tests/storage/test_db.py` три проверки `assert migrate(conn) == 2` заменить на `== 3`. В `test_migrate_creates_latest_schema` строку с набором таблиц заменить на:

```python
    assert {"listings", "price_history", "runs", "poi", "route_cache", "fx_rates", "scores", "notifications",
            "user_marks"} <= tables(conn)
    assert "notified_at" in {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
```

В `tests/test_cli.py` в `test_init_db_creates_database` строку `"схема v2"` заменить на `"схема v3"`.

В конец `tests/factories.py` добавить (и `import json` уже есть):

```python
def score_record(listing_id: str, **overrides) -> dict:
    """Строка снимка scores для тестов: в рейтинге на 1-м месте, балл 70; поля можно переопределить."""
    base = {
        "listing_id": listing_id,
        "computed_at": "2026-10-30T05:00:00+00:00",
        "price_eur": 28000.0,
        "price_per_sot_eur": 4666.67,
        "lat": 47.09921,
        "lon": 28.8165,
        "coords_approx": False,
        "route_km": 8.0,
        "route_min": 10.0,
        "nearest_entry": "M3 Drumul Băcioiului",
        "poi_nearest_m": "{}",
        "market_median_eur_per_sot": 5600.0,
        "median_level": "settlement",
        "median_n": 6,
        "duplicate_group": None,
        "is_group_primary": True,
        "components": json.dumps(
            {"price": 66.7, "route": 80.0, "infrastructure": 30.0, "utilities": 90.0, "settlement": 50.0}
        ),
        "explanations": json.dumps(
            {"price": "Цена 67: на 17% дешевле медианы", "route": "Дорога 80: 10 мин до въезда"}, ensure_ascii=False
        ),
        "flags": json.dumps(["no_sewerage"]),
        "total": 70.0,
        "excluded_reason": None,
        "rank": 1,
    }
    base.update(overrides)
    return base
```

Создать `tests/storage/test_marks.py`:

```python
from datetime import UTC, datetime

from homealize.storage import marks, repo
from tests.factories import make_listing

T0 = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)


def add(db, lid):
    repo.upsert_listing(db, make_listing(id=lid), status="active", filter_reason=None, now=T0)


def test_marks_are_added_once_listed_and_removed(db):
    add(db, "a")
    add(db, "b")
    assert marks.add_mark(db, "a", marks.FAV, now=T0) is True
    assert marks.add_mark(db, "a", marks.FAV, now=T0) is False
    assert marks.add_mark(db, "b", marks.HIDDEN, now=T0) is True
    assert marks.marked(db, marks.FAV) == {"a"}
    assert marks.marked(db, marks.HIDDEN) == {"b"}
    assert marks.remove_mark(db, "a", marks.FAV) is True
    assert marks.remove_mark(db, "a", marks.FAV) is False
    assert marks.marked(db, marks.FAV) == set()


def test_mark_time_is_stored_in_utc(db):
    add(db, "a")
    marks.add_mark(db, "a", marks.FAV, now=T0)
    assert db.execute("SELECT created_at FROM user_marks").fetchone()[0] == T0.isoformat()
```

Создать `tests/storage/test_scores.py`:

```python
from datetime import UTC, datetime

from homealize.storage import repo
from homealize.storage.scores import apply_hidden, assign_ranks, get_scored, load_scored, replace_scores
from tests.factories import make_listing, score_record

T0 = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)


def seed(db, *records):
    for record in records:
        repo.upsert_listing(db, make_listing(id=record["listing_id"]), status="active", filter_reason=None, now=T0)
    replace_scores(db, list(records))


def ranks(db):
    return {row["listing_id"]: row["rank"] for row in db.execute("SELECT listing_id, rank FROM scores")}


def test_assign_ranks_orders_by_total_then_price_then_id(db):
    seed(
        db,
        score_record("c", total=60.0, rank=None),
        score_record("b", total=70.0, price_eur=30000.0, rank=None),
        score_record("a", total=70.0, price_eur=30000.0, rank=None),
        score_record("d", total=70.0, price_eur=25000.0, rank=None),
        score_record("x", total=90.0, excluded_reason="over_budget", rank=None),
    )
    assign_ranks(db)
    assert ranks(db) == {"d": 1, "a": 2, "b": 3, "c": 4, "x": None}


def test_apply_hidden_excludes_and_restores(db):
    seed(
        db,
        score_record("a", total=80.0),
        score_record("b", total=70.0),
        score_record("over", total=90.0, excluded_reason="over_budget", rank=None),
    )
    apply_hidden(db, {"a", "over"})
    rows = {row["listing_id"]: row for row in db.execute("SELECT * FROM scores")}
    assert (rows["a"]["excluded_reason"], rows["a"]["rank"]) == ("hidden", None)
    assert rows["b"]["rank"] == 1
    assert rows["over"]["excluded_reason"] == "over_budget"  # более конкретная причина остаётся
    apply_hidden(db, set())
    assert ranks(db) == {"a": 1, "b": 2, "over": None}


def test_get_scored_joins_listing_fields(db):
    seed(db, score_record("a"))
    row = get_scored(db, "a")
    assert (row["settlement"], row["url"]) == ("Гратиешты", "https://999.md/ru/100000001")
    assert "aaa111.jpg" in row["images"]
    assert get_scored(db, "missing") is None
    assert "images" in load_scored(db)[0].keys()
```

Добавить в `tests/scoring/test_engine.py` (импорт `from homealize.storage import marks` — к существующим):

```python
def test_hidden_listing_is_excluded_and_others_move_up(db):
    six_normal(db)
    marks.add_mark(db, "n0", marks.HIDDEN, now=T0)
    summary = rescore(db, SETTINGS, services(), now=T0)
    s = scores(db)
    assert (s["n0"]["excluded_reason"], s["n0"]["rank"]) == ("hidden", None)
    assert sorted(row["rank"] for row in s.values() if row["rank"] is not None) == [1, 2, 3, 4, 5]
    assert summary.eligible == 5


def test_hidden_mark_does_not_replace_a_more_specific_reason(db):
    six_normal(db)
    add(db, "big", 9000, area=10.0)  # 90 000 € — дороже бюджета
    marks.add_mark(db, "big", marks.HIDDEN, now=T0)
    rescore(db, SETTINGS, services(), now=T0)
    assert scores(db)["big"]["excluded_reason"] == "over_budget"
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `uv run pytest tests/storage tests/scoring/test_engine.py -q`
Expected: FAIL — версия схемы 2, нет таблиц `notifications`/`user_marks`, нет модуля `marks`, нет `assign_ranks`.

- [ ] **Step 3: Миграция 003**

Создать `src/homealize/storage/migrations/003_notifications.sql`:

```sql
CREATE TABLE notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL CHECK (kind IN ('new', 'price_drop', 'first_run', 'digest', 'alert')),
    topic      TEXT,
    listing_id TEXT,
    payload    TEXT NOT NULL DEFAULT '{}',
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'dropped')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    sent_at    TEXT
);

CREATE INDEX idx_notifications_status ON notifications (status, id);
CREATE INDEX idx_notifications_topic ON notifications (kind, topic, created_at);

CREATE TABLE user_marks (
    listing_id TEXT NOT NULL REFERENCES listings (id),
    mark       TEXT NOT NULL CHECK (mark IN ('fav', 'hidden')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (listing_id, mark)
);

ALTER TABLE runs ADD COLUMN notified_at TEXT;
```

- [ ] **Step 4: `marks.py` и `scores.py`**

Создать `src/homealize/storage/marks.py`:

```python
"""Отметки пользователя из бота: ★ избранное и скрытые объявления. Функции не делают commit."""

import sqlite3
from datetime import UTC, datetime

FAV = "fav"
HIDDEN = "hidden"


def add_mark(conn: sqlite3.Connection, listing_id: str, mark: str, *, now: datetime) -> bool:
    """True, если отметки не было и она поставлена. Объявление должно быть в listings."""
    cursor = conn.execute(
        "INSERT OR IGNORE INTO user_marks (listing_id, mark, created_at) VALUES (?, ?, ?)",
        (listing_id, mark, now.astimezone(UTC).isoformat()),
    )
    return cursor.rowcount == 1


def remove_mark(conn: sqlite3.Connection, listing_id: str, mark: str) -> bool:
    """True, если отметка была и снята."""
    cursor = conn.execute("DELETE FROM user_marks WHERE listing_id = ? AND mark = ?", (listing_id, mark))
    return cursor.rowcount == 1


def marked(conn: sqlite3.Connection, mark: str) -> set[str]:
    return {row[0] for row in conn.execute("SELECT listing_id FROM user_marks WHERE mark = ?", (mark,))}
```

Заменить `src/homealize/storage/scores.py` целиком:

```python
"""Снимок оценки: перезаписывается целиком при каждом пересчёте. Функции не делают commit."""

import sqlite3

SCORE_COLUMNS = (
    "listing_id",
    "computed_at",
    "price_eur",
    "price_per_sot_eur",
    "lat",
    "lon",
    "coords_approx",
    "route_km",
    "route_min",
    "nearest_entry",
    "poi_nearest_m",
    "market_median_eur_per_sot",
    "median_level",
    "median_n",
    "duplicate_group",
    "is_group_primary",
    "components",
    "explanations",
    "flags",
    "total",
    "excluded_reason",
    "rank",
)
_SCORED = (
    "SELECT s.*, l.title, l.url, l.region, l.settlement, l.zone, l.area_sot, l.price_original, l.currency, "
    "l.author_type, l.first_seen_at, l.images "
    "FROM scores s JOIN listings l ON l.id = s.listing_id"
)


def replace_scores(conn: sqlite3.Connection, records: list[dict[str, object]]) -> None:
    conn.execute("DELETE FROM scores")
    if records:
        conn.executemany(
            f"INSERT INTO scores ({', '.join(SCORE_COLUMNS)}) VALUES ({', '.join('?' * len(SCORE_COLUMNS))})",
            [[record[column] for column in SCORE_COLUMNS] for record in records],
        )


def assign_ranks(conn: sqlite3.Connection) -> None:
    """Места 1, 2, … у объявлений без причины исключения: выше балл, при равенстве дешевле, потом по id."""
    conn.execute("UPDATE scores SET rank = NULL")
    ids = [
        row[0]
        for row in conn.execute(
            "SELECT listing_id FROM scores WHERE excluded_reason IS NULL ORDER BY total DESC, price_eur, listing_id"
        ).fetchall()
    ]
    conn.executemany(
        "UPDATE scores SET rank = ? WHERE listing_id = ?",
        [(rank, listing_id) for rank, listing_id in enumerate(ids, start=1)],
    )


def apply_hidden(conn: sqlite3.Connection, hidden: set[str]) -> None:
    """После /hide и /unhide: причина hidden ставится или снимается без пересчёта баллов, места — заново.

    Скрытым становится только объявление без другой причины исключения: «дороже бюджета» важнее.
    """
    for row in conn.execute("SELECT listing_id, excluded_reason FROM scores").fetchall():
        if row["excluded_reason"] is None and row["listing_id"] in hidden:
            conn.execute("UPDATE scores SET excluded_reason = 'hidden' WHERE listing_id = ?", (row["listing_id"],))
        elif row["excluded_reason"] == "hidden" and row["listing_id"] not in hidden:
            conn.execute("UPDATE scores SET excluded_reason = NULL WHERE listing_id = ?", (row["listing_id"],))
    assign_ranks(conn)


def load_scored(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Оценённые объявления вместе с полями объявления: сначала рейтинг по месту, потом остальные."""
    return conn.execute(f"{_SCORED} ORDER BY s.rank IS NULL, s.rank, s.total DESC, s.listing_id").fetchall()


def get_scored(conn: sqlite3.Connection, listing_id: str) -> sqlite3.Row | None:
    return conn.execute(f"{_SCORED} WHERE s.listing_id = ?", (listing_id,)).fetchone()
```

- [ ] **Step 5: Движок и подписи**

В `src/homealize/scoring/components.py` в `EXCLUSION_LABELS` после `"duplicate": …` добавить строку:

```python
    "hidden": "скрыто вами",
```

В `src/homealize/scoring/engine.py`:

1. Импорт `from homealize.storage.scores import replace_scores` заменить на:

```python
from homealize.storage import marks
from homealize.storage.scores import assign_ranks, replace_scores
```

2. В `rescore` после строки `active = [c for c in candidates if c.row["status"] == "active"]` добавить:

```python
    hidden = marks.marked(conn, marks.HIDDEN)
```

3. В словаре записи строку `"excluded_reason": _excluded_reason(c, suspicious, primary, settings),` заменить на:

```python
            "excluded_reason": _excluded_reason(c, suspicious, primary, settings, hidden),
```

4. Конец `rescore` от `eligible = sorted(` до конца функции заменить на:

```python
    eligible = [r for r in records if r["excluded_reason"] is None]
    replace_scores(conn, records)
    assign_ranks(conn)  # места считает снимок: так же их пересчитывают /hide и /unhide
    return ScoreSummary(
        scored=len(records),
        eligible=len(eligible),
        best_total=max((r["total"] for r in eligible), default=None),
    )
```

5. `_excluded_reason` заменить на:

```python
def _excluded_reason(
    c: _Candidate, suspicious: bool, primary: bool, settings: Settings, hidden: set[str]
) -> str | None:
    if c.location is None and not c.in_city:
        return "no_coords"
    if c.route is None:
        return "no_route"
    if c.route.km > settings.routing.max_route_km:
        return "too_far"
    if c.price_eur is None:
        return "no_price_eur"
    if c.price_eur > settings.scoring.max_price_eur:
        return "over_budget"
    if suspicious:
        return "suspicious_price"
    if not primary:
        return "duplicate"
    if c.row["id"] in hidden:
        return "hidden"
    return None
```

- [ ] **Step 6: Запустить тесты**

Run: `uv run pytest tests/storage tests/scoring -q`
Expected: PASS.

Run: `uv run pytest -q`
Expected: PASS (Excel и CLI не изменились: на листе «Все активные» скрытые получат подпись «скрыто вами»).

- [ ] **Step 7: Commit**

```bash
git add src/homealize/storage/migrations/003_notifications.sql src/homealize/storage/marks.py src/homealize/storage/scores.py src/homealize/scoring/engine.py src/homealize/scoring/components.py tests/factories.py tests/storage tests/scoring/test_engine.py tests/test_cli.py
git commit -m "feat: notifications and user marks tables; hidden listings leave the ranking"
```

---

### Task 5: Клиент Telegram и очередь уведомлений

**Files:**
- Create: `src/homealize/notify/__init__.py` (одна строка-докстрока)
- Create: `src/homealize/notify/telegram.py`
- Create: `src/homealize/notify/outbox.py`
- Modify: `tests/factories.py` (+ `FakeTelegram`)
- Create: `tests/notify/__init__.py` (пустой), `tests/notify/test_telegram.py`, `tests/notify/test_outbox.py`

**Interfaces:**
- Consumes: `TelegramSettings` (Task 1), таблица `notifications` (Task 4).
- Produces:
  - `homealize.notify.telegram`:
    - `TelegramError(message, *, permanent=False, retry_after=None)`: `permanent=True` только для HTTP 400; `retry_after` — из `parameters.retry_after` (429);
    - `TelegramClient(token, *, api_url, timeout_seconds, transport=None)` с методами:
      - `send_message(chat_id, text, *, preview_url=None) -> None` — делит длинный текст; превью-фото только у первой части;
      - `get_updates(offset: int | None, timeout: int) -> list[dict]`;
      - `get_me() -> dict`;
      - `close()`, `__enter__` / `__exit__`;
    - `split_text(text, limit=4000) -> list[str]`.
  - `homealize.notify.outbox`:
    - `Outgoing(key, kind, text, listing_id=None, topic=None, preview_url=None, data={})` — frozen dataclass;
    - `Notifier(client, chat_id: int)` — frozen dataclass;
    - `Delivery(sent=0, failed=0, dropped=0)`;
    - `enqueue(conn, items, *, now) -> int` — без commit;
    - `deliver(conn, notifier, settings: TelegramSettings, *, now, sleep=time.sleep) -> Delivery` — commit после каждого сообщения;
    - `exists(conn, key) -> bool`, `pending_count(conn) -> int`, `last_created(conn, kind, topic) -> datetime | None`.
  - `tests.factories.FakeTelegram` — подмена `TelegramClient` для тестов задач 5–10.

- [ ] **Step 1: `FakeTelegram` и падающие тесты**

В конец `tests/factories.py` добавить:

```python
class FakeTelegram:
    """Telegram для тестов: запоминает отправленное.

    errors — исключения для первых отправок по порядку; updates — ответы get_updates по порядку
    (список обновлений или исключение).
    """

    def __init__(self, *, errors=(), updates=()):
        self.sent: list[tuple[int, str, str | None]] = []
        self.errors = list(errors)
        self.updates = list(updates)
        self.polls: list[tuple[int | None, int]] = []
        self.closed = False

    def send_message(self, chat_id, text, *, preview_url=None):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append((chat_id, text, preview_url))

    def get_updates(self, offset, timeout):
        self.polls.append((offset, timeout))
        if not self.updates:
            return []
        batch = self.updates.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch

    def get_me(self):
        return {"username": "homealize_test_bot"}

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def texts(self) -> list[str]:
        return [text for _chat, text, _preview in self.sent]
```

Создать `tests/notify/__init__.py` (пустой) и `tests/notify/test_telegram.py`:

```python
import json

import httpx
import pytest

from homealize.notify.telegram import TelegramClient, TelegramError, split_text

TOKEN = "123456:SECRET-token"


def make_client(handler):
    return TelegramClient(
        TOKEN, api_url="https://api.telegram.org", timeout_seconds=5, transport=httpx.MockTransport(handler)
    )


def recorder(requests, result=True):
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": result})

    return handler


def test_send_message_posts_html_with_a_photo_preview():
    requests = []
    make_client(recorder(requests, {"message_id": 1})).send_message(
        42, "<b>Привет</b>", preview_url="https://i.example/1.jpg"
    )
    request = requests[0]
    assert request.url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    body = json.loads(request.content)
    assert (body["chat_id"], body["text"], body["parse_mode"]) == (42, "<b>Привет</b>", "HTML")
    assert body["link_preview_options"] == {
        "url": "https://i.example/1.jpg", "prefer_large_media": True, "show_above_text": True,
    }


def test_message_without_photo_has_no_preview():
    requests = []
    make_client(recorder(requests)).send_message(42, "текст")
    assert json.loads(requests[0].content)["link_preview_options"] == {"is_disabled": True}


def test_long_text_is_split_by_lines_and_only_the_first_part_has_the_photo():
    requests = []
    text = "\n".join(f"строка {i} " + "x" * 90 for i in range(100))
    make_client(recorder(requests)).send_message(42, text, preview_url="https://i.example/1.jpg")
    bodies = [json.loads(request.content) for request in requests]
    assert len(bodies) == 3
    assert all(len(body["text"]) <= 4000 for body in bodies)
    assert "\n".join(body["text"] for body in bodies) == text
    assert bodies[0]["link_preview_options"]["url"] == "https://i.example/1.jpg"
    assert bodies[1]["link_preview_options"] == {"is_disabled": True}


def test_split_text():
    assert split_text("a\n\nb") == ["a\n\nb"]
    assert split_text("x" * 9000) == ["x" * 4000, "x" * 4000, "x" * 1000]


def test_bad_request_is_permanent_and_hides_the_token():
    def handler(request):
        return httpx.Response(
            400, json={"ok": False, "error_code": 400, "description": f"Bad Request: bot{TOKEN} can't parse entities"}
        )

    with pytest.raises(TelegramError) as caught:
        make_client(handler).send_message(1, "x")
    assert caught.value.permanent
    assert TOKEN not in str(caught.value)
    assert "can't parse entities" in str(caught.value)


def test_flood_limit_reports_retry_after():
    def handler(request):
        return httpx.Response(
            429,
            json={"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 3}},
        )

    with pytest.raises(TelegramError) as caught:
        make_client(handler).send_message(1, "x")
    assert (caught.value.permanent, caught.value.retry_after) == (False, 3.0)


def test_network_error_is_temporary_and_does_not_leak_the_url():
    def handler(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}")

    with pytest.raises(TelegramError) as caught:
        make_client(handler).send_message(1, "x")
    assert not caught.value.permanent
    assert TOKEN not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_non_json_answer_is_temporary():
    with pytest.raises(TelegramError, match="502"):
        make_client(lambda request: httpx.Response(502, text="Bad Gateway")).get_me()


def test_get_updates_sends_offset_and_waits_longer_than_the_poll():
    requests = []
    updates = [{"update_id": 7, "message": {"text": "/top"}}]
    client = make_client(recorder(requests, updates))
    assert client.get_updates(7, 50) == updates
    body = json.loads(requests[0].content)
    assert (body["offset"], body["timeout"], body["allowed_updates"]) == (7, 50, ["message"])
    assert requests[0].extensions["timeout"]["read"] == 60


def test_get_updates_without_offset():
    requests = []
    make_client(recorder(requests, [])).get_updates(None, 0)
    assert "offset" not in json.loads(requests[0].content)
```

Создать `tests/notify/test_outbox.py`:

```python
from datetime import UTC, datetime, timedelta

from homealize.config import TelegramSettings
from homealize.notify import outbox
from homealize.notify.outbox import Notifier, Outgoing
from homealize.notify.telegram import TelegramError
from tests.factories import FakeTelegram

T0 = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)
SETTINGS = TelegramSettings()


def two(db):
    outbox.enqueue(
        db,
        [
            Outgoing("new:a", "new", "A", listing_id="a", preview_url="https://p/1.jpg"),
            Outgoing("new:b", "new", "B", listing_id="b"),
        ],
        now=T0,
    )
    db.commit()


def statuses(db):
    return {row["key"]: row["status"] for row in db.execute("SELECT key, status FROM notifications")}


def test_enqueue_skips_known_keys(db):
    assert outbox.enqueue(db, [Outgoing("new:a", "new", "A", listing_id="a")], now=T0) == 1
    assert outbox.enqueue(db, [Outgoing("new:a", "new", "A ещё раз")], now=T0) == 0
    assert outbox.exists(db, "new:a") and not outbox.exists(db, "new:b")
    assert outbox.pending_count(db) == 1


def test_deliver_sends_in_order_marks_sent_and_pauses_between(db):
    two(db)
    fake, sleeps = FakeTelegram(), []
    delivery = outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=sleeps.append)
    assert (delivery.sent, delivery.failed, delivery.dropped) == (2, 0, 0)
    assert fake.sent == [(42, "A", "https://p/1.jpg"), (42, "B", None)]
    assert sleeps == [1.0]
    assert statuses(db) == {"new:a": "sent", "new:b": "sent"}
    assert not db.in_transaction
    assert outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=sleeps.append).sent == 0


def test_temporary_failure_stops_and_keeps_messages_pending(db):
    two(db)
    fake = FakeTelegram(errors=[TelegramError("нет сети")])
    delivery = outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=lambda _s: None)
    assert (delivery.sent, delivery.failed) == (0, 1)
    assert statuses(db) == {"new:a": "pending", "new:b": "pending"}
    row = db.execute("SELECT attempts, last_error FROM notifications WHERE key = 'new:a'").fetchone()
    assert (row["attempts"], row["last_error"]) == (1, "нет сети")
    assert outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=lambda _s: None).sent == 2


def test_permanent_failure_drops_only_that_message(db):
    two(db)
    fake = FakeTelegram(errors=[TelegramError("Bad Request", permanent=True)])
    delivery = outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=lambda _s: None)
    assert (delivery.sent, delivery.dropped) == (1, 1)
    assert statuses(db) == {"new:a": "dropped", "new:b": "sent"}


def test_message_is_dropped_after_max_attempts(db):
    two(db)
    fake = FakeTelegram(errors=[TelegramError("нет сети")] * 3)
    for _ in range(3):
        outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=lambda _s: None)
    assert statuses(db)["new:a"] == "dropped"


def test_old_pending_messages_expire(db):
    two(db)
    fake = FakeTelegram()
    delivery = outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0 + timedelta(hours=25), sleep=lambda _s: None)
    assert (delivery.sent, delivery.dropped) == (0, 2)
    assert fake.sent == []


def test_flood_limit_waits_and_retries_once(db):
    outbox.enqueue(db, [Outgoing("alert:x:1", "alert", "тревога", topic="x")], now=T0)
    fake, sleeps = FakeTelegram(errors=[TelegramError("Too Many Requests", retry_after=3)]), []
    assert outbox.deliver(db, Notifier(fake, 42), SETTINGS, now=T0, sleep=sleeps.append).sent == 1
    assert sleeps == [3]


def test_last_created_by_kind_and_topic(db):
    assert outbox.last_created(db, "alert", "rate_limited") is None
    outbox.enqueue(db, [Outgoing("alert:rate_limited:1", "alert", "т", topic="rate_limited")], now=T0)
    assert outbox.last_created(db, "alert", "rate_limited") == T0
    assert outbox.last_created(db, "alert", "crashed") is None
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `uv run pytest tests/notify -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'homealize.notify'`.

- [ ] **Step 3: `telegram.py`**

Создать `src/homealize/notify/__init__.py`:

```python
"""Уведомления в Telegram: что сообщить, очередь и доставка, команды бота."""
```

Создать `src/homealize/notify/telegram.py`:

```python
"""Telegram Bot API через httpx: отправка сообщений и приём команд (long polling).

Токен — часть адреса запроса, поэтому ни адреса, ни исходные тексты ошибок httpx наружу не выходят.
"""

import httpx

CHUNK = 4000  # Telegram принимает до 4096 символов; оставляем запас


class TelegramError(Exception):
    """Telegram не принял запрос. permanent — это сообщение повторять бессмысленно (HTTP 400)."""

    def __init__(self, message: str, *, permanent: bool = False, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.retry_after = retry_after


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        api_url: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token = token
        self._client = httpx.Client(
            base_url=f"{api_url.rstrip('/')}/bot{token}/", timeout=timeout_seconds, transport=transport
        )

    def send_message(self, chat_id: int, text: str, *, preview_url: str | None = None) -> None:
        """HTML-сообщение; длинное уходит несколькими. preview_url — картинка над текстом первой части."""
        for index, chunk in enumerate(split_text(text)):
            payload: dict = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
            if index == 0 and preview_url:
                payload["link_preview_options"] = {
                    "url": preview_url, "prefer_large_media": True, "show_above_text": True,
                }
            else:
                payload["link_preview_options"] = {"is_disabled": True}
            self._call("sendMessage", payload)

    def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        """Новые сообщения боту; ждёт до timeout секунд, если их нет."""
        payload: dict = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload, timeout=timeout + 10)
        return result if isinstance(result, list) else []

    def get_me(self) -> dict:
        result = self._call("getMe", {})
        return result if isinstance(result, dict) else {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TelegramClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _call(self, method: str, payload: dict, *, timeout: float | None = None) -> object:
        try:
            response = self._client.post(
                method, json=payload, timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT
            )
        except httpx.RequestError as exc:
            raise TelegramError(f"Telegram недоступен: {type(exc).__name__}: {self._clean(exc)}") from None
        try:
            data = response.json()
        except ValueError:
            raise TelegramError(f"Telegram ответил не JSON (HTTP {response.status_code})") from None
        if not isinstance(data, dict):
            raise TelegramError(f"Telegram ответил не объектом (HTTP {response.status_code})")
        if data.get("ok") is True:
            return data.get("result")
        code = data.get("error_code", response.status_code)
        parameters = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
        retry_after = parameters.get("retry_after")
        raise TelegramError(
            f"Telegram: {self._clean(data.get('description') or f'HTTP {code}')}",
            permanent=code == 400,
            retry_after=float(retry_after) if isinstance(retry_after, (int, float)) else None,
        )

    def _clean(self, value: object) -> str:
        return str(value).replace(self._token, "***")[:200]


def split_text(text: str, limit: int = CHUNK) -> list[str]:
    """Делит текст по строкам на части не длиннее limit; слишком длинная строка режется."""
    chunks: list[str] = []
    lines: list[str] = []
    size = 0
    for line in text.split("\n"):
        while len(line) > limit:
            if lines:
                chunks.append("\n".join(lines))
                lines, size = [], 0
            chunks.append(line[:limit])
            line = line[limit:]
        extra = len(line) + (1 if lines else 0)
        if lines and size + extra > limit:
            chunks.append("\n".join(lines))
            lines, size, extra = [], 0, len(line)
        lines.append(line)
        size += extra
    chunks.append("\n".join(lines))
    return [chunk for chunk in chunks if chunk] or [""]
```

- [ ] **Step 4: `outbox.py`**

Создать `src/homealize/notify/outbox.py`:

```python
"""Очередь уведомлений (спека §7.1, §13): сообщения ждут в notifications, пока Telegram их не примет.

enqueue не делает commit. deliver фиксирует каждое сообщение сразу после отправки, чтобы сбой
посреди отправки не повторил уже доставленное.
"""

import json
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from homealize.config import TelegramSettings
from homealize.notify.telegram import TelegramClient, TelegramError

log = logging.getLogger(__name__)

MAX_RETRY_AFTER = 60.0  # дольше ждать Telegram в этой отправке не будем: остальное уйдёт позже


@dataclass(frozen=True)
class Outgoing:
    key: str  # уникален: то же событие второй раз в очередь не попадёт
    kind: str  # new | price_drop | first_run | digest | alert
    text: str  # HTML
    listing_id: str | None = None
    topic: str | None = None  # вид тревоги или дата дайджеста
    preview_url: str | None = None  # фото над текстом
    data: dict = field(default_factory=dict)  # что ещё сохранить в payload (например, топ дайджеста)


@dataclass(frozen=True)
class Notifier:
    client: TelegramClient
    chat_id: int


@dataclass
class Delivery:
    sent: int = 0
    failed: int = 0  # временный сбой: сообщение осталось в очереди
    dropped: int = 0  # больше не будет отправляться


def enqueue(conn: sqlite3.Connection, items: list[Outgoing], *, now: datetime) -> int:
    """Ставит сообщения в очередь; уже известные ключи пропускаются. Возвращает, сколько добавлено."""
    created = now.astimezone(UTC).isoformat()
    added = 0
    for item in items:
        payload = {"text": item.text, "preview_url": item.preview_url, **item.data}
        cursor = conn.execute(
            "INSERT OR IGNORE INTO notifications (key, kind, topic, listing_id, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (item.key, item.kind, item.topic, item.listing_id, json.dumps(payload, ensure_ascii=False), created),
        )
        added += cursor.rowcount
    return added


def deliver(
    conn: sqlite3.Connection,
    notifier: Notifier,
    settings: TelegramSettings,
    *,
    now: datetime,
    sleep: Callable[[float], None] = time.sleep,
) -> Delivery:
    """Отправляет ожидающие сообщения по порядку. При временном сбое останавливается: остальные уйдут позже."""
    delivery = Delivery()
    stamp = now.astimezone(UTC).isoformat()
    expire_before = (now.astimezone(UTC) - timedelta(hours=settings.expire_hours)).isoformat()
    pending = conn.execute("SELECT * FROM notifications WHERE status = 'pending' ORDER BY id").fetchall()
    for row in pending:
        if row["created_at"] < expire_before:
            _set(conn, row["id"], status="dropped", attempts=row["attempts"], error="устарело: не отправлено за сутки")
            delivery.dropped += 1
            continue
        if delivery.sent:
            sleep(settings.send_pause_seconds)
        payload = json.loads(row["payload"])
        try:
            _send(notifier, payload, sleep)
        except TelegramError as exc:
            attempts = row["attempts"] + 1
            give_up = exc.permanent or attempts >= settings.max_attempts
            _set(conn, row["id"], status="dropped" if give_up else "pending", attempts=attempts, error=str(exc))
            log.warning("Уведомление %s не отправлено: %s", row["key"], exc)
            if give_up:
                delivery.dropped += 1
            else:
                delivery.failed += 1
            if exc.permanent:
                continue  # плохо только это сообщение
            break  # Telegram недоступен: остальные — в следующий раз
        conn.execute(
            "UPDATE notifications SET status = 'sent', attempts = attempts + 1, sent_at = ?, last_error = NULL "
            "WHERE id = ?",
            (stamp, row["id"]),
        )
        conn.commit()
        delivery.sent += 1
    return delivery


def exists(conn: sqlite3.Connection, key: str) -> bool:
    return conn.execute("SELECT 1 FROM notifications WHERE key = ?", (key,)).fetchone() is not None


def pending_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM notifications WHERE status = 'pending'").fetchone()[0]


def last_created(conn: sqlite3.Connection, kind: str, topic: str) -> datetime | None:
    """Когда последний раз ставилось в очередь сообщение этого вида и темы (в любом статусе)."""
    value = conn.execute(
        "SELECT MAX(created_at) FROM notifications WHERE kind = ? AND topic = ?", (kind, topic)
    ).fetchone()[0]
    return datetime.fromisoformat(value) if value else None


def _send(notifier: Notifier, payload: dict, sleep: Callable[[float], None]) -> None:
    try:
        notifier.client.send_message(notifier.chat_id, payload["text"], preview_url=payload.get("preview_url"))
    except TelegramError as exc:
        if exc.retry_after is None or exc.retry_after > MAX_RETRY_AFTER:
            raise
        sleep(exc.retry_after)
        notifier.client.send_message(notifier.chat_id, payload["text"], preview_url=payload.get("preview_url"))


def _set(conn: sqlite3.Connection, notification_id: int, *, status: str, attempts: int, error: str) -> None:
    conn.execute(
        "UPDATE notifications SET status = ?, attempts = ?, last_error = ? WHERE id = ?",
        (status, attempts, error, notification_id),
    )
    conn.commit()
```

- [ ] **Step 5: Запустить тесты**

Run: `uv run pytest tests/notify -q`
Expected: PASS.

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/homealize/notify tests/factories.py tests/notify
git commit -m "feat: Telegram Bot API client and a notification outbox"
```

---

### Task 6: Тексты сообщений, новые участки, снижения цен, первый прогон

**Files:**
- Create: `src/homealize/notify/format.py`
- Create: `src/homealize/notify/events.py`
- Modify: `src/homealize/storage/repo.py` (+ `has_listings_before`, `notify_window_start`, `mark_notified`)
- Test: `tests/notify/test_format.py`, `tests/notify/test_events.py` (новые), `tests/storage/test_repo.py`

**Interfaces:**
- Consumes:
  - `load_scored`, `get_scored` (Task 4);
  - `marks` (Task 4);
  - `Outgoing` (Task 5);
  - `rate_cache.latest_rates`, `to_eur`, `EXCLUSION_LABELS`, `FLAG_LABELS`, `IN_CITY_ENTRY`, `AUTHOR_LABELS` (этап 2).
- Produces:
  - `homealize.notify.format`:
    - `money(value, currency="EUR") -> str`, `decimal(value, digits=1) -> str`, `area(value) -> str`;
    - `plural(n, one, few, many) -> str`, `short(text, limit=200) -> str`;
    - `local_time(value: str | datetime, zone, pattern="%d.%m %H:%M") -> str`;
    - `deviation(price_per_sot, median) -> str | None`;
    - `ad_command(listing_id) -> str` (`/ad_<id>`), `osm_url(lat, lon) -> str`, `first_photo(row) -> str | None`;
    - `listing_line(row, *, fav, note="") -> str`;
    - `new_listing(row, *, fav) -> str`;
    - `price_drop(row, *, old, new, percent, fav) -> str`;
    - `card(row, history, *, fav, zone) -> str`;
    - `NO_SETTLEMENT`.

    Здесь `row` — строка `load_scored`/`get_scored` (в тестах подойдёт `dict` с теми же ключами).
  - `homealize.notify.events`:
    - `plan_after_run(conn, settings, *, run_id: int, now: datetime) -> list[Outgoing]`;
    - `drop_share(before, after, rates) -> float | None`.
  - `homealize.storage.repo`:
    - `has_listings_before(conn, ts: str) -> bool`;
    - `notify_window_start(conn, run_id: int, *, not_before: str) -> str`;
    - `mark_notified(conn, run_id: int, *, now: datetime) -> None`.
  - Ключи очереди:
    - `first_run`;
    - `new:<listing_id>`;
    - `new-more:<run_id>`;
    - `price_drop:<listing_id>:<price_history.id>`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/storage/test_repo.py`:

```python
def test_notify_window_starts_after_the_last_notified_run(db):
    first = repo.start_run(db, T0)
    repo.mark_notified(db, first, now=T0)
    second = repo.start_run(db, T1)
    third = repo.start_run(db, T1 + timedelta(hours=3))
    day_ago = (T0 - timedelta(days=1)).isoformat()
    assert repo.notify_window_start(db, third, not_before=day_ago) == T1.isoformat()
    assert repo.notify_window_start(db, second, not_before=day_ago) == T1.isoformat()
    assert repo.notify_window_start(db, third, not_before=(T1 + timedelta(hours=1)).isoformat()) == (
        T1 + timedelta(hours=1)
    ).isoformat()
    assert db.execute("SELECT notified_at FROM runs WHERE id = ?", (first,)).fetchone()[0] == T0.isoformat()


def test_has_listings_before(db):
    save(db, make_listing(id="a"), now=T1)
    assert repo.has_listings_before(db, (T1 + timedelta(seconds=1)).isoformat())
    assert not repo.has_listings_before(db, T1.isoformat())
```

Создать `tests/notify/test_format.py`:

```python
import json
from zoneinfo import ZoneInfo

from homealize.notify import format as fmt
from tests.factories import score_record

ZONE = ZoneInfo("Europe/Chisinau")


def row(**overrides) -> dict:
    """Строка как у load_scored: снимок оценки плюс поля объявления."""
    base = score_record(
        "123", rank=3, total=78.4, price_eur=32000.0, price_per_sot_eur=4000.0, market_median_eur_per_sot=4800.0,
        route_min=11.2, route_km=9.4, nearest_entry="бул. Дачия (юг)",
    )
    base.update({
        "title": "8 сот.", "url": "https://999.md/ru/123", "region": "Новые Анены", "settlement": "Сынжера",
        "zone": None, "area_sot": 8.0, "price_original": 32000.0, "currency": "EUR", "author_type": "private",
        "first_seen_at": "2026-10-30T05:00:00+00:00", "images": json.dumps(["https://i.example/1.jpg"]),
    })
    base.update(overrides)
    return base


def test_money_area_and_plural():
    assert fmt.money(32000.0) == "32 000 €"
    assert fmt.money(700000.0, "MDL") == "700 000 MDL"
    assert fmt.money(None) == "цена не указана"
    assert (fmt.area(8.0), fmt.area(6.5), fmt.area(None)) == ("8 сот.", "6,5 сот.", "площадь не указана")
    assert [fmt.plural(n, "участок", "участка", "участков") for n in (1, 3, 5, 11, 21)] == [
        "участок", "участка", "участков", "участков", "участок",
    ]


def test_short_takes_the_first_line_and_cuts():
    assert fmt.short("первая\nвторая") == "первая"
    assert fmt.short("x" * 300) == "x" * 199 + "…"


def test_local_time_and_photo():
    assert fmt.local_time("2026-10-30T05:00:00+00:00", ZONE) == "30.10 07:00"
    assert fmt.first_photo(row()) == "https://i.example/1.jpg"
    assert fmt.first_photo(row(images="[]")) is None


def test_listing_line():
    assert fmt.listing_line(row(), fav=True) == "3. ★ 78 · Сынжера, 8 сот., 32 000 € (−17%), 11 мин /ad_123"
    assert fmt.listing_line(row(rank=None, route_min=None), fav=False, note="↑2") == (
        "78 · Сынжера, 8 сот., 32 000 € (−17%) · ↑2 /ad_123"
    )


def test_new_listing_message():
    lines = fmt.new_listing(row(), fav=False).split("\n")
    assert lines[0] == "🆕 <b>Новый участок</b> · балл 78 · место 3"
    assert lines[1] == "Сынжера (Новые Анены), 8 сот."
    assert lines[2] == "32 000 € · 4 000 €/сот. · на 17% дешевле медианы"
    assert lines[3] == "🚗 11 мин до въезда «бул. Дачия (юг)», 9,4 км"
    assert lines[4] == "⚠ нет канализации (не указано в объявлении)"
    assert lines[5] == "Баллы: цена 67 · дорога 80 · инфраструктура 30 · коммуникации 90 · село 50"
    assert lines[6].startswith(
        '<a href="https://999.md/ru/123">999.md</a> · <a href="https://www.openstreetmap.org/?mlat=47.09921&amp;mlon=28.81650'
    )
    assert lines[6].endswith("/ad_123")


def test_values_from_the_site_are_escaped():
    text = fmt.new_listing(row(settlement="<b>Село</b>", region=None), fav=False)
    assert "&lt;b&gt;Село&lt;/b&gt;, 8 сот." in text


def test_city_listing_and_approximate_coordinates():
    text = fmt.new_listing(
        row(nearest_entry="в черте города", route_min=0.0, route_km=0.0, coords_approx=True), fav=False
    )
    assert "🏙 в черте Кишинёва" in text
    assert "карта (центр села)" in text


def test_other_currency_shows_the_euro_price():
    assert fmt.new_listing(row(price_original=640000.0, currency="MDL"), fav=False).split("\n")[2].startswith(
        "640 000 MDL (≈ 32 000 €) · "
    )


def test_price_drop_message():
    text = fmt.price_drop(row(), old="34 000 €", new="32 000 €", percent=6, fav=True)
    lines = text.split("\n")
    assert lines[0] == "📉 <b>Цена снижена</b>: было 34 000 €, стало 32 000 € (−6%)"
    assert lines[1] == "★ Сынжера (Новые Анены), 8 сот. · балл 78 · место 3"


def test_card_shows_reason_explanations_and_price_history():
    history = [{"price_original": 33000.0, "currency": "EUR"}, {"price_original": 32000.0, "currency": "EUR"}]
    text = fmt.card(row(rank=None, excluded_reason="over_budget"), history, fav=False, zone=ZONE)
    assert "Не в рейтинге: дороже бюджета" in text
    assert "Цена 67: на 17% дешевле медианы" in text
    assert "История цены: 33 000 € → 32 000 €" in text
    assert "Автор: частное лицо" in text
    assert "Впервые увидено: 30.10.2026" in text
```

Создать `tests/notify/test_events.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from homealize.config import Settings
from homealize.models import RunSummary
from homealize.notify.events import drop_share, plan_after_run
from homealize.storage import repo
from homealize.storage.scores import replace_scores
from tests.factories import make_listing, score_record

T0 = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)
SETTINGS = Settings()


def hours(n: float) -> datetime:
    return T0 + timedelta(hours=n)


def run(db, now, *, notified=True) -> int:
    run_id = repo.start_run(db, now)
    repo.finish_run(db, run_id, RunSummary(search_complete=True), now=now)
    if notified:
        repo.mark_notified(db, run_id, now=now)
    return run_id


def listing(db, lid, now, **overrides):
    repo.upsert_listing(
        db, make_listing(id=lid, url=f"https://999.md/ru/{lid}", **overrides),
        status="active", filter_reason=None, now=now,
    )


def keys(items):
    return [item.key for item in items]


def test_first_run_sends_only_a_summary(db):
    run_id = run(db, T0, notified=False)
    listing(db, "101", T0)
    listing(db, "102", T0)
    replace_scores(db, [score_record("101", rank=1, total=75.0), score_record("102", rank=2, total=65.0)])
    items = plan_after_run(db, SETTINGS, run_id=run_id, now=T0)
    assert keys(items) == ["first_run"]
    assert "загружено 2 участка, в рейтинге 2" in items[0].text
    assert "/ad_101" in items[0].text and "/ad_102" in items[0].text


def test_new_listing_from_this_run_is_announced_with_its_photo(db):
    run(db, T0)
    listing(db, "101", T0)
    run_id = run(db, hours(3), notified=False)
    listing(db, "102", hours(3))
    replace_scores(db, [score_record("101", rank=1, total=80.0), score_record("102", rank=2, total=75.0)])
    items = plan_after_run(db, SETTINGS, run_id=run_id, now=hours(3))
    assert keys(items) == ["new:102"]
    assert (items[0].kind, items[0].listing_id) == ("new", "102")
    assert items[0].preview_url == "https://i.simpalsmedia.com/999.md/BoardImages/900x900/aaa111.jpg"
    assert "Новый участок" in items[0].text


def test_new_listing_needs_a_high_score_or_a_top_place(db):
    run(db, T0)
    listing(db, "100", T0)
    run_id = run(db, hours(3), notified=False)
    for lid in ("101", "102", "103"):
        listing(db, lid, hours(3))
    replace_scores(db, [
        score_record("100", rank=1, total=90.0),
        score_record("101", rank=10, total=50.0),  # в топ-10
        score_record("102", rank=11, total=70.0),  # балл 70
        score_record("103", rank=12, total=69.0),  # ни то, ни другое
    ])
    assert keys(plan_after_run(db, SETTINGS, run_id=run_id, now=hours(3))) == ["new:101", "new:102"]


def test_excluded_and_reposted_listings_are_not_new(db):
    run(db, T0)
    listing(db, "100", T0)
    run_id = run(db, hours(3), notified=False)
    listing(db, "101", hours(3))
    listing(db, "102", hours(3))
    replace_scores(db, [
        score_record("100", rank=None, excluded_reason="duplicate", duplicate_group="100"),
        score_record("101", rank=1, total=80.0, duplicate_group="100"),  # перевыставленная копия известного
        score_record("102", rank=None, total=90.0, excluded_reason="over_budget"),
    ])
    assert plan_after_run(db, SETTINGS, run_id=run_id, now=hours(3)) == []


def test_more_than_ten_new_listings_end_with_one_summary_line(db):
    run(db, T0)
    listing(db, "100", T0)
    run_id = run(db, hours(3), notified=False)
    records = [score_record("100", rank=1, total=95.0)]
    for i in range(12):
        lid = str(200 + i)
        listing(db, lid, hours(3))
        records.append(score_record(lid, rank=i + 2, total=80.0))
    replace_scores(db, records)
    items = plan_after_run(db, SETTINGS, run_id=run_id, now=hours(3))
    assert len(items) == 11
    assert keys(items)[:2] == ["new:200", "new:201"]
    assert items[-1].key == f"new-more:{run_id}"
    assert "ещё 2 новых участка — /new" in items[-1].text


def test_listings_from_a_run_without_notifications_come_with_the_next_one(db):
    run(db, T0)
    listing(db, "100", T0)
    run(db, hours(3), notified=False)  # например, упала оценка: окно не закрыто
    listing(db, "101", hours(3))
    run_id = run(db, hours(6), notified=False)
    replace_scores(db, [score_record("100", rank=1, total=80.0), score_record("101", rank=2, total=75.0)])
    assert keys(plan_after_run(db, SETTINGS, run_id=run_id, now=hours(6))) == ["new:101"]


def test_window_never_reaches_back_more_than_a_day(db):
    run(db, T0)
    listing(db, "100", T0)
    run(db, hours(1), notified=False)
    listing(db, "101", hours(1))
    run_id = run(db, hours(30), notified=False)
    replace_scores(db, [score_record("100", rank=1, total=80.0), score_record("101", rank=2, total=75.0)])
    assert plan_after_run(db, SETTINGS, run_id=run_id, now=hours(30)) == []


def test_price_drop_of_five_percent_or_more_is_announced(db):
    run(db, T0)
    listing(db, "101", T0, price_original=30000.0)
    run_id = run(db, hours(3), notified=False)
    listing(db, "101", hours(3), price_original=28000.0)
    replace_scores(db, [score_record("101", rank=1, total=65.0, price_eur=28000.0)])
    items = plan_after_run(db, SETTINGS, run_id=run_id, now=hours(3))
    history_id = repo.get_price_history(db, "101")[-1]["id"]
    assert keys(items) == [f"price_drop:101:{history_id}"]
    assert "было 30 000 €, стало 28 000 € (−7%)" in items[0].text


def test_small_drop_or_low_score_is_not_announced(db):
    run(db, T0)
    listing(db, "101", T0, price_original=30000.0)
    listing(db, "102", T0, price_original=30000.0)
    run_id = run(db, hours(3), notified=False)
    listing(db, "101", hours(3), price_original=29000.0)  # −3%
    listing(db, "102", hours(3), price_original=27000.0)  # −10%, но балл 55
    replace_scores(db, [score_record("101", rank=1, total=80.0), score_record("102", rank=2, total=55.0)])
    assert plan_after_run(db, SETTINGS, run_id=run_id, now=hours(3)) == []


def test_drop_across_currencies_is_compared_in_euro():
    rates = {"MDL": 1.0, "EUR": 20.0}
    eur = {"price_original": 30000.0, "currency": "EUR"}
    mdl = {"price_original": 500000.0, "currency": "MDL"}
    assert drop_share(eur, mdl, rates) == pytest.approx(1 - 25000 / 30000)
    assert drop_share(eur, mdl, None) is None
    assert drop_share(eur, {"price_original": 28500.0, "currency": "EUR"}, None) == pytest.approx(0.05)
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `uv run pytest tests/notify/test_format.py tests/notify/test_events.py tests/storage/test_repo.py -q`
Expected: FAIL — нет модулей `homealize.notify.format`, `homealize.notify.events`, нет `repo.mark_notified`.

- [ ] **Step 3: Помощники `repo`**

В `src/homealize/storage/repo.py` импорт `from datetime import datetime` заменить на `from datetime import UTC, datetime` и после `close_stale_runs` добавить:

```python
def has_listings_before(conn: sqlite3.Connection, ts: str) -> bool:
    """Были ли объявления, впервые увиденные раньше ts (если нет — это первый прогон)."""
    return conn.execute("SELECT 1 FROM listings WHERE first_seen_at < ? LIMIT 1", (ts,)).fetchone() is not None


def notify_window_start(conn: sqlite3.Connection, run_id: int, *, not_before: str) -> str:
    """Начало окна «новых»: первый прогон после последнего, по которому уже готовились уведомления.

    Не раньше not_before: после долгого перерыва старое не присылается.
    """
    last = conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM runs WHERE notified_at IS NOT NULL AND id < ?", (run_id,)
    ).fetchone()[0]
    first = conn.execute("SELECT MIN(started_at) FROM runs WHERE id > ? AND id <= ?", (last, run_id)).fetchone()[0]
    return max(first, not_before)


def mark_notified(conn: sqlite3.Connection, run_id: int, *, now: datetime) -> None:
    conn.execute("UPDATE runs SET notified_at = ? WHERE id = ?", (now.astimezone(UTC).isoformat(), run_id))
```

- [ ] **Step 4: `format.py`**

Создать `src/homealize/notify/format.py`:

```python
"""Тексты сообщений Telegram (HTML, спека §7.1). Всё, что пришло с сайта, экранируется."""

import json
from datetime import datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from homealize.export.excel import AUTHOR_LABELS
from homealize.scoring.components import EXCLUSION_LABELS, FLAG_LABELS, IN_CITY_ENTRY

Row = Any  # строка scores.load_scored / get_scored (в тестах — dict с теми же ключами)

COMPONENT_NAMES = {
    "price": "цена",
    "route": "дорога",
    "infrastructure": "инфраструктура",
    "utilities": "коммуникации",
    "settlement": "село",
}
NO_SETTLEMENT = "село не указано"
SHORT_LIMIT = 200


def money(value: float | None, currency: str | None = "EUR") -> str:
    """«32 000 €», «700 000 MDL»."""
    if value is None:
        return "цена не указана"
    unit = "€" if currency in (None, "EUR") else currency
    return f"{value:,.0f}".replace(",", " ") + f" {unit}"


def decimal(value: float, digits: int = 1) -> str:
    """Десятичная запятая: 9,4."""
    return f"{value:.{digits}f}".replace(".", ",")


def area(value: float | None) -> str:
    if value is None:
        return "площадь не указана"
    return f"{decimal(value).removesuffix(',0')} сот."


def plural(n: int, one: str, few: str, many: str) -> str:
    """1 участок, 3 участка, 5 участков."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    if n % 10 == 1:
        return one
    if 2 <= n % 10 <= 4:
        return few
    return many


def short(text: str, limit: int = SHORT_LIMIT) -> str:
    """Первая строка, не длиннее limit символов."""
    first = next(iter(str(text).splitlines()), "")
    return first if len(first) <= limit else first[: limit - 1] + "…"


def local_time(value: str | datetime, zone: ZoneInfo, pattern: str = "%d.%m %H:%M") -> str:
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    return moment.astimezone(zone).strftime(pattern)


def deviation(price_per_sot: float | None, median: float | None) -> str | None:
    if not price_per_sot or not median:
        return None
    percent = round((price_per_sot / median - 1) * 100)
    if percent == 0:
        return "на уровне медианы"
    return f"на {abs(percent)}% {'дешевле' if percent < 0 else 'дороже'} медианы"


def ad_command(listing_id: str) -> str:
    """Команда-ссылка на карточку: в Telegram по ней можно нажать."""
    return f"/ad_{listing_id}"


def osm_url(lat: float, lon: float) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=16/{lat:.5f}/{lon:.5f}"


def first_photo(row: Row) -> str | None:
    images = json.loads(row["images"] or "[]")
    return images[0] if images else None


def star(fav: bool) -> str:
    return "★ " if fav else ""


def settlement_name(row: Row) -> str:
    return escape(row["settlement"] or NO_SETTLEMENT)


def place(row: Row) -> str:
    """«Сынжера (Новые Анены)»."""
    name = settlement_name(row)
    return f"{name} ({escape(row['region'])})" if row["region"] else name


def headline(row: Row) -> str:
    """«балл 78 · место 3»."""
    text = f"балл {row['total']:.0f}"
    return f"{text} · место {row['rank']}" if row["rank"] is not None else text


def price_line(row: Row) -> str:
    """«32 000 € · 4 000 €/сот. · на 17% дешевле медианы»."""
    price = money(row["price_original"], row["currency"])
    if row["currency"] not in (None, "EUR") and row["price_eur"] is not None:
        price += f" (≈ {money(row['price_eur'])})"
    parts = [price]
    if row["price_per_sot_eur"] is not None:
        parts.append(f"{money(row['price_per_sot_eur'])}/сот.")
    note = deviation(row["price_per_sot_eur"], row["market_median_eur_per_sot"])
    if note:
        parts.append(note)
    return " · ".join(parts)


def route_line(row: Row) -> str:
    if row["nearest_entry"] == IN_CITY_ENTRY:
        return "🏙 в черте Кишинёва"
    if row["route_min"] is None:
        return "🚗 маршрут до города не найден"
    return f"🚗 {row['route_min']:.0f} мин до въезда «{escape(row['nearest_entry'])}», {decimal(row['route_km'])} км"


def flag_lines(row: Row) -> list[str]:
    return [escape(FLAG_LABELS.get(flag, flag)) for flag in json.loads(row["flags"])]


def breakdown(row: Row) -> str:
    """«цена 67 · дорога 80 · …»."""
    scores = json.loads(row["components"])
    return " · ".join(f"{label} {scores[name]:.0f}" for name, label in COMPONENT_NAMES.items() if name in scores)


def links(row: Row) -> str:
    parts = [f'<a href="{escape(row["url"])}">999.md</a>']
    if row["lat"] is not None and row["lon"] is not None:
        label = "карта (центр села)" if row["coords_approx"] else "карта"
        parts.append(f'<a href="{escape(osm_url(row["lat"], row["lon"]))}">{label}</a>')
    parts.append(ad_command(row["listing_id"]))
    return " · ".join(parts)


def listing_line(row: Row, *, fav: bool, note: str = "") -> str:
    """Строка списка: «3. ★ 78 · Сынжера, 8 сот., 32 000 € (−17%), 11 мин /ad_123»."""
    rank = f"{row['rank']}. " if row["rank"] is not None else ""
    price = money(row["price_eur"])
    percent = _short_deviation(row["price_per_sot_eur"], row["market_median_eur_per_sot"])
    if percent:
        price += f" ({percent})"
    minutes = f", {row['route_min']:.0f} мин" if row["route_min"] is not None else ""
    tail = f" · {note}" if note else ""
    return (
        f"{rank}{star(fav)}{row['total']:.0f} · {settlement_name(row)}, {area(row['area_sot'])}, "
        f"{price}{minutes}{tail} {ad_command(row['listing_id'])}"
    )


def new_listing(row: Row, *, fav: bool) -> str:
    return "\n".join([
        f"🆕 <b>Новый участок</b> · {headline(row)}",
        f"{star(fav)}{place(row)}, {area(row['area_sot'])}",
        price_line(row),
        route_line(row),
        *flag_lines(row),
        f"Баллы: {breakdown(row)}",
        links(row),
    ])


def price_drop(row: Row, *, old: str, new: str, percent: int, fav: bool) -> str:
    return "\n".join([
        f"📉 <b>Цена снижена</b>: было {old}, стало {new} (−{percent}%)",
        f"{star(fav)}{place(row)}, {area(row['area_sot'])} · {headline(row)}",
        price_line(row),
        route_line(row),
        links(row),
    ])


def card(row: Row, history: list, *, fav: bool, zone: ZoneInfo) -> str:
    """Карточка участка для /ad: всё, что известно об оценке."""
    explanations = json.loads(row["explanations"])
    if row["rank"] is not None:
        status = f"Место в рейтинге: {row['rank']}"
    else:
        reason = row["excluded_reason"]
        status = f"Не в рейтинге: {escape(EXCLUSION_LABELS.get(reason, reason or 'причина не указана'))}"
    lines = [
        f"{star(fav)}<b>{place(row)}</b>, {area(row['area_sot'])} · балл {row['total']:.0f}",
        status,
        price_line(row),
        route_line(row),
        *flag_lines(row),
        "",
        *[escape(explanations[name]) for name in COMPONENT_NAMES if name in explanations],
        "",
        f"Автор: {escape(AUTHOR_LABELS.get(row['author_type'], str(row['author_type'])))}",
        f"Впервые увидено: {local_time(row['first_seen_at'], zone, '%d.%m.%Y')}",
    ]
    prices = [money(entry["price_original"], entry["currency"]) for entry in history[-5:]]
    if len(prices) > 1:
        lines.append(f"История цены: {' → '.join(prices)}")
    lines.append(links(row))
    return "\n".join(lines)


def _short_deviation(price_per_sot: float | None, median: float | None) -> str:
    """«−17%», «+5%», «±0%»; пусто, если сравнивать не с чем."""
    if not price_per_sot or not median:
        return ""
    percent = round((price_per_sot / median - 1) * 100)
    return "±0%" if percent == 0 else f"{percent:+d}%".replace("-", "−")
```

- [ ] **Step 5: `events.py`**

Создать `src/homealize/notify/events.py`:

```python
"""Что сообщить после прогона (спека §7.1, §13): новые участки, снижения цен, сводка первого прогона.

Функции только готовят сообщения; в очередь их ставит jobs.run_cycle.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

from homealize.config import Settings
from homealize.enrich.currency import to_eur
from homealize.notify import format as fmt
from homealize.notify.outbox import Outgoing
from homealize.storage import marks, rate_cache, repo
from homealize.storage.scores import load_scored

WINDOW = timedelta(days=1)  # окно «новых» не уходит в прошлое дальше суток


def plan_after_run(conn: sqlite3.Connection, settings: Settings, *, run_id: int, now: datetime) -> list[Outgoing]:
    since = repo.notify_window_start(conn, run_id, not_before=(now.astimezone(UTC) - WINDOW).isoformat())
    scored = load_scored(conn)
    favs = marks.marked(conn, marks.FAV)
    if not repo.has_listings_before(conn, since):
        return [_first_run(conn, settings, scored, favs)]
    return [
        *_new_listings(scored, settings, favs, since=since, run_id=run_id),
        *_price_drops(conn, scored, settings, favs, since=since),
    ]


def drop_share(before, after, rates: dict[str, float] | None) -> float | None:
    """На какую долю снизилась цена; в разных валютах — по евро. None — сравнить нельзя."""
    if before["currency"] == after["currency"]:
        old, new = before["price_original"], after["price_original"]
    else:
        old = to_eur(before["price_original"], before["currency"], rates)
        new = to_eur(after["price_original"], after["currency"], rates)
    if not old or new is None:
        return None
    return (old - new) / old


def _first_run(conn: sqlite3.Connection, settings: Settings, scored: list, favs: set[str]) -> Outgoing:
    loaded = conn.execute(
        "SELECT COUNT(*) FROM listings WHERE status = 'active' AND filter_reason IS NULL"
    ).fetchone()[0]
    rated = [row for row in scored if row["rank"] is not None]
    lines = [
        f"✅ <b>Первый прогон</b>: загружено {loaded} {fmt.plural(loaded, 'участок', 'участка', 'участков')}, "
        f"в рейтинге {len(rated)}."
    ]
    top = rated[: settings.telegram.digest_top]
    if top:
        lines.append(f"Топ-{len(top)}:")
        lines += [fmt.listing_line(row, fav=row["listing_id"] in favs) for row in top]
    lines.append("Дальше буду присылать новые участки и снижения цен.")
    return Outgoing(key="first_run", kind="first_run", text="\n".join(lines))


def _new_listings(scored: list, settings: Settings, favs: set[str], *, since: str, run_id: int) -> list[Outgoing]:
    t = settings.telegram
    known_groups = {row["duplicate_group"] for row in scored if row["duplicate_group"] and row["first_seen_at"] < since}
    fresh = [
        row
        for row in scored  # load_scored уже отсортирован по месту
        if row["rank"] is not None
        and row["first_seen_at"] >= since
        and (row["total"] >= t.new_min_total or row["rank"] <= t.new_max_rank)
        and row["duplicate_group"] not in known_groups
    ]
    items = [
        Outgoing(
            key=f"new:{row['listing_id']}",
            kind="new",
            text=fmt.new_listing(row, fav=row["listing_id"] in favs),
            listing_id=row["listing_id"],
            preview_url=fmt.first_photo(row),
        )
        for row in fresh[: t.max_new_per_run]
    ]
    rest = len(fresh) - t.max_new_per_run
    if rest > 0:
        word = fmt.plural(rest, "новый участок", "новых участка", "новых участков")
        items.append(Outgoing(key=f"new-more:{run_id}", kind="new", text=f"…и ещё {rest} {word} — /new"))
    return items


def _price_drops(
    conn: sqlite3.Connection, scored: list, settings: Settings, favs: set[str], *, since: str
) -> list[Outgoing]:
    t = settings.telegram
    changed = {
        row[0] for row in conn.execute("SELECT DISTINCT listing_id FROM price_history WHERE observed_at >= ?", (since,))
    }
    latest = rate_cache.latest_rates(conn)
    rates = latest[1] if latest else None
    items = []
    for row in scored:
        if row["listing_id"] not in changed or row["rank"] is None or row["total"] < t.price_drop_min_total:
            continue
        history = repo.get_price_history(conn, row["listing_id"])
        if len(history) < 2 or history[-1]["observed_at"] < since:
            continue
        before, after = history[-2], history[-1]
        share = drop_share(before, after, rates)
        if share is None or share < t.price_drop_min_share:
            continue
        items.append(
            Outgoing(
                key=f"price_drop:{row['listing_id']}:{after['id']}",
                kind="price_drop",
                text=fmt.price_drop(
                    row,
                    old=fmt.money(before["price_original"], before["currency"]),
                    new=fmt.money(after["price_original"], after["currency"]),
                    percent=round(share * 100),
                    fav=row["listing_id"] in favs,
                ),
                listing_id=row["listing_id"],
                preview_url=fmt.first_photo(row),
            )
        )
    return items
```

- [ ] **Step 6: Запустить тесты**

Run: `uv run pytest tests/notify tests/storage/test_repo.py -q`
Expected: PASS.

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/homealize/notify/format.py src/homealize/notify/events.py src/homealize/storage/repo.py tests/notify tests/storage/test_repo.py
git commit -m "feat: messages for new listings, price drops and the first run"
```

---

### Task 7: Технические тревоги и дайджест

**Files:**
- Create: `src/homealize/notify/alerts.py`
- Create: `src/homealize/notify/digest.py`
- Test: `tests/notify/test_alerts.py`, `tests/notify/test_digest.py` (новые)

**Interfaces:**
- Consumes:
  - `outbox.last_created`, `Outgoing` (Task 5);
  - `format.*` (Task 6);
  - `marks`, `load_scored` (Task 4);
  - `repo.get_listing`, `EXCLUSION_LABELS`.
- Produces:
  - `homealize.notify.alerts`:
    - `Failure(kind: str, detail: str)` — frozen dataclass;
    - `ALERT_TEXTS: dict[str, str]`;
    - `plan_alerts(conn, settings, failures: list[Failure], *, run_id: int | None, now) -> list[Outgoing]`. Ключ — `alert:<kind>:<run_id или время>`, `topic = kind`.
  - `homealize.notify.digest`:
    - `digest_key(day: date) -> str` (`digest:ГГГГ-ММ-ДД`, дата по Кишинёву);
    - `plan_digest(conn, settings, *, now) -> Outgoing`. В `data["top"]` лежит список `{"id", "rank", "price_eur", "settlement", "area_sot"}`;
    - `previous_top(conn, *, exclude_key) -> list[dict] | None`.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/notify/test_alerts.py`:

```python
from datetime import UTC, datetime, timedelta

from homealize.config import Settings
from homealize.notify import outbox
from homealize.notify.alerts import Failure, plan_alerts

T0 = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)
SETTINGS = Settings()


def test_alert_has_kind_detail_and_status_hint(db):
    detail = "RateLimitedError: 999.md ограничил частоту запросов"
    items = plan_alerts(db, SETTINGS, [Failure("rate_limited", detail)], run_id=7, now=T0)
    assert [(item.key, item.kind, item.topic) for item in items] == [("alert:rate_limited:7", "alert", "rate_limited")]
    assert items[0].text.split("\n") == [
        "🛠 <b>Тех. тревога</b>: 999.md ограничил частоту запросов — обход остановлен до следующего прогона",
        detail,
        "Подробности: /status",
    ]


def test_same_alert_is_not_repeated_within_six_hours(db):
    outbox.enqueue(db, plan_alerts(db, SETTINGS, [Failure("source_unavailable", "x")], run_id=1, now=T0), now=T0)
    later = T0 + timedelta(hours=5)
    assert plan_alerts(db, SETTINGS, [Failure("source_unavailable", "x")], run_id=2, now=later) == []
    assert len(plan_alerts(db, SETTINGS, [Failure("export_failed", "Excel")], run_id=2, now=later)) == 1
    assert len(plan_alerts(db, SETTINGS, [Failure("source_unavailable", "x")], run_id=3, now=T0 + timedelta(hours=6))) == 1


def test_detail_is_shortened_and_escaped(db):
    detail = "<html>" + "x" * 400 + "\nвторая строка"
    text = plan_alerts(db, SETTINGS, [Failure("crashed", detail)], run_id=None, now=T0)[0].text
    second = text.split("\n")[1]
    assert second.startswith("&lt;html&gt;") and second.endswith("…")
    assert "вторая строка" not in text


def test_alert_without_run_uses_the_time_in_its_key(db):
    items = plan_alerts(db, SETTINGS, [Failure("crashed", "")], run_id=None, now=T0)
    assert items[0].key == f"alert:crashed:{T0.isoformat()}"
    assert items[0].text.split("\n") == ["🛠 <b>Тех. тревога</b>: прогон упал", "Подробности: /status"]
```

Создать `tests/notify/test_digest.py`:

```python
from datetime import UTC, datetime, timedelta

from homealize.config import Settings
from homealize.notify import outbox
from homealize.notify.digest import plan_digest
from homealize.notify.outbox import Outgoing
from homealize.storage import marks, repo
from homealize.storage.scores import replace_scores
from tests.factories import make_listing, score_record

EVENING = datetime(2026, 10, 30, 18, 0, tzinfo=UTC)  # 20:00 по Кишинёву
OLD = EVENING - timedelta(days=3)
SETTINGS = Settings.model_validate({"telegram": {"digest_top": 3}})


def listing(db, lid, now=OLD):
    repo.upsert_listing(db, make_listing(id=lid, url=f"https://999.md/ru/{lid}"), status="active",
                        filter_reason=None, now=now)


def item(lid, rank, price=28000.0):
    return {"id": lid, "rank": rank, "price_eur": price, "settlement": "Гратиешты", "area_sot": 6.0}


def previous(db, top, *, day="2026-10-29"):
    outbox.enqueue(db, [Outgoing(f"digest:{day}", "digest", "вчера", topic=day, data={"top": top})],
                   now=EVENING - timedelta(days=1))


def test_first_digest_lists_the_top_and_counts_new(db):
    listing(db, "101")
    listing(db, "102", EVENING - timedelta(hours=5))
    listing(db, "103", EVENING - timedelta(hours=2))
    replace_scores(db, [
        score_record("101", rank=1, total=80.0),
        score_record("102", rank=2, total=75.0),
        score_record("103", rank=None, excluded_reason="over_budget"),
    ])
    digest = plan_digest(db, SETTINGS, now=EVENING)
    assert (digest.key, digest.kind, digest.topic) == ("digest:2026-10-30", "digest", "2026-10-30")
    lines = digest.text.split("\n")
    assert lines[0] == "📋 <b>Дайджест за 30.10</b>"
    assert lines[1] == "Новых за сутки: 2, из них в рейтинге 1."
    assert lines[3] == "Топ-2:"
    assert lines[4] == "1. 80 · Гратиешты, 6 сот., 28 000 € (−17%), 10 мин /ad_101"
    assert "Выпали" not in digest.text
    assert digest.data["top"] == [item("101", 1), item("102", 2)]


def test_digest_shows_moves_against_the_previous_one(db):
    for lid in ("101", "102", "103", "104"):
        listing(db, lid)
    previous(db, [item("102", 1), item("101", 2), item("103", 3)])
    replace_scores(db, [
        score_record("101", rank=1, total=80.0),
        score_record("102", rank=2, total=75.0),
        score_record("104", rank=3, total=72.0),
        score_record("103", rank=4, total=60.0),
    ])
    text = plan_digest(db, SETTINGS, now=EVENING).text
    assert "1. 80 · Гратиешты, 6 сот., 28 000 € (−17%), 10 мин · ↑1 /ad_101" in text
    assert "· ↓1 /ad_102" in text
    assert "· 🆕 в топе /ad_104" in text
    assert "• Гратиешты, 6 сот. (было 3-е место) — опустился на 4-е место /ad_103" in text


def test_dropped_listings_explain_why(db):
    for lid in ("101", "102", "103", "105", "106", "107"):
        listing(db, lid)
    db.execute("UPDATE listings SET status = 'removed' WHERE id = '101'")
    marks.add_mark(db, "102", marks.HIDDEN, now=EVENING)
    previous(db, [item("101", 1), item("102", 2), item("103", 3)])
    replace_scores(db, [
        score_record("105", rank=1, total=85.0),
        score_record("106", rank=2, total=84.0),
        score_record("107", rank=3, total=83.0),
        score_record("102", rank=None, excluded_reason="hidden"),
        score_record("103", rank=None, excluded_reason="over_budget", price_eur=52000.0),
    ])
    text = plan_digest(db, SETTINGS, now=EVENING).text
    assert "— снято с сайта /ad_101" in text
    assert "— скрыто вами /ad_102" in text
    assert "— подорожал: 28 000 € → 52 000 € (дороже бюджета) /ad_103" in text


def test_digest_day_is_the_local_date(db):
    late = datetime(2026, 10, 30, 22, 30, tzinfo=UTC)  # 00:30 31 октября по Кишинёву
    assert plan_digest(db, SETTINGS, now=late).key == "digest:2026-10-31"


def test_empty_rating(db):
    assert "В рейтинге пока пусто." in plan_digest(db, SETTINGS, now=EVENING).text


def test_todays_digest_is_not_compared_with_itself(db):
    listing(db, "101")
    replace_scores(db, [score_record("101", rank=1, total=80.0)])
    previous(db, [item("999", 1)], day="2026-10-30")
    text = plan_digest(db, SETTINGS, now=EVENING).text
    assert "Выпали" not in text and "↑" not in text
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `uv run pytest tests/notify/test_alerts.py tests/notify/test_digest.py -q`
Expected: FAIL — нет модулей `homealize.notify.alerts`, `homealize.notify.digest`.

- [ ] **Step 3: `alerts.py`**

Создать `src/homealize/notify/alerts.py`:

```python
"""Технические тревоги (спека §7.1): сбой обхода, оценки или Excel.

Тревога одного вида приходит не чаще telegram.alert_repeat_hours. Предупреждения внешних сервисов
(Overpass, OSRM, BNM) тревогой не считаются: их видно в /status.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape

from homealize.config import Settings
from homealize.notify import format as fmt
from homealize.notify import outbox
from homealize.notify.outbox import Outgoing

ALERT_TEXTS = {
    "rate_limited": "999.md ограничил частоту запросов — обход остановлен до следующего прогона",
    "empty_search": "поиск 999.md вернул 0 объявлений — возможно, сайт изменился",
    "source_unavailable": "999.md недоступен — обход не завершён",
    "source_schema": "ответ 999.md не похож на ожидаемый — возможно, сайт изменился",
    "unparsed": "слишком много объявлений не удалось разобрать — снятие пропущено",
    "too_few_seen": "обход увидел слишком мало объявлений — снятие пропущено",
    "crashed": "прогон упал",
    "evaluation_failed": "оценка не выполнена",
    "export_failed": "Excel не сохранён",
}


@dataclass(frozen=True)
class Failure:
    kind: str  # ключ ALERT_TEXTS
    detail: str  # исходный текст ошибки; в сообщение попадает его первая строка


def plan_alerts(
    conn: sqlite3.Connection, settings: Settings, failures: list[Failure], *, run_id: int | None, now: datetime
) -> list[Outgoing]:
    repeat = timedelta(hours=settings.telegram.alert_repeat_hours)
    stamp = str(run_id) if run_id is not None else now.astimezone(UTC).isoformat()
    items = []
    for failure in failures:
        last = outbox.last_created(conn, "alert", failure.kind)
        if last is not None and now - last < repeat:
            continue
        lines = [f"🛠 <b>Тех. тревога</b>: {escape(ALERT_TEXTS.get(failure.kind, failure.kind))}"]
        if failure.detail:
            lines.append(escape(fmt.short(failure.detail)))
        lines.append("Подробности: /status")
        items.append(
            Outgoing(key=f"alert:{failure.kind}:{stamp}", kind="alert", topic=failure.kind, text="\n".join(lines))
        )
    return items
```

- [ ] **Step 4: `digest.py`**

Создать `src/homealize/notify/digest.py`:

```python
"""Дайджест дня (спека §7.1): новые за сутки, топ со сдвигами мест, кто выпал из топа и почему."""

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from html import escape

from homealize.config import Settings
from homealize.notify import format as fmt
from homealize.notify.outbox import Outgoing
from homealize.scoring.components import EXCLUSION_LABELS
from homealize.storage import marks, repo
from homealize.storage.scores import load_scored


def digest_key(day: date) -> str:
    return f"digest:{day.isoformat()}"


def plan_digest(conn: sqlite3.Connection, settings: Settings, *, now: datetime) -> Outgoing:
    day = now.astimezone(settings.schedule.zone).date()
    key = digest_key(day)
    scored = load_scored(conn)
    by_id = {row["listing_id"]: row for row in scored}
    top = [row for row in scored if row["rank"] is not None][: settings.telegram.digest_top]
    favs = marks.marked(conn, marks.FAV)
    hidden = marks.marked(conn, marks.HIDDEN)
    previous = previous_top(conn, exclude_key=key)
    new_total, new_rated = _new_counts(conn, by_id, now=now)

    lines = [f"📋 <b>Дайджест за {day:%d.%m}</b>", f"Новых за сутки: {new_total}, из них в рейтинге {new_rated}."]
    if top:
        before = {entry["id"]: entry["rank"] for entry in previous or []}
        lines += ["", f"Топ-{len(top)}:"]
        for row in top:
            note = _movement(before.get(row["listing_id"]), row["rank"]) if previous is not None else ""
            lines.append(fmt.listing_line(row, fav=row["listing_id"] in favs, note=note))
    else:
        lines.append("В рейтинге пока пусто.")
    current = {row["listing_id"] for row in top}
    dropped = [entry for entry in previous or [] if entry["id"] not in current]
    if dropped:
        lines += ["", "Выпали из топа:"]
        lines += [f"• {_dropped_line(conn, entry, by_id.get(entry['id']), hidden)}" for entry in dropped]
    snapshot = [
        {
            "id": row["listing_id"],
            "rank": row["rank"],
            "price_eur": row["price_eur"],
            "settlement": row["settlement"],
            "area_sot": row["area_sot"],
        }
        for row in top
    ]
    return Outgoing(key=key, kind="digest", topic=day.isoformat(), text="\n".join(lines), data={"top": snapshot})


def previous_top(conn: sqlite3.Connection, *, exclude_key: str) -> list[dict] | None:
    """Топ из последнего прошлого дайджеста (с ним сравниваются места)."""
    row = conn.execute(
        "SELECT payload FROM notifications WHERE kind = 'digest' AND key != ? ORDER BY id DESC LIMIT 1",
        (exclude_key,),
    ).fetchone()
    if row is None:
        return None
    top = json.loads(row["payload"]).get("top")
    return top if isinstance(top, list) else None


def _new_counts(conn: sqlite3.Connection, by_id: dict, *, now: datetime) -> tuple[int, int]:
    since = (now.astimezone(UTC) - timedelta(days=1)).isoformat()
    ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM listings WHERE first_seen_at >= ? AND filter_reason IS NULL", (since,)
        )
    ]
    rated = sum(1 for listing_id in ids if listing_id in by_id and by_id[listing_id]["rank"] is not None)
    return len(ids), rated


def _movement(before: int | None, rank: int) -> str:
    if before is None:
        return "🆕 в топе"
    if before == rank:
        return "="
    return f"↑{before - rank}" if rank < before else f"↓{rank - before}"


def _dropped_line(conn: sqlite3.Connection, entry: dict, row, hidden: set[str]) -> str:
    name = escape(entry.get("settlement") or fmt.NO_SETTLEMENT)
    label = f"{name}, {fmt.area(entry.get('area_sot'))} (было {entry['rank']}-е место)"
    return f"{label} — {_drop_reason(conn, entry, row, hidden)} {fmt.ad_command(entry['id'])}"


def _drop_reason(conn: sqlite3.Connection, entry: dict, row, hidden: set[str]) -> str:
    listing = repo.get_listing(conn, entry["id"])
    if listing is None or listing["status"] == "removed":
        return "снято с сайта"
    if listing["status"] == "filtered_out":
        return "отсеяно фильтром"
    if entry["id"] in hidden:
        return "скрыто вами"
    if row is None:
        return "нет оценки"
    old_price, reason = entry.get("price_eur"), row["excluded_reason"]
    if old_price and row["price_eur"] and row["price_eur"] > old_price:
        text = f"подорожал: {fmt.money(old_price)} → {fmt.money(row['price_eur'])}"
        return f"{text} ({EXCLUSION_LABELS.get(reason, reason)})" if reason else text
    if reason:
        return EXCLUSION_LABELS.get(reason, reason)
    if row["rank"] is not None:
        return f"опустился на {row['rank']}-е место"
    return "вне рейтинга"
```

- [ ] **Step 5: Запустить тесты**

Run: `uv run pytest tests/notify -q`
Expected: PASS.

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/homealize/notify/alerts.py src/homealize/notify/digest.py tests/notify/test_alerts.py tests/notify/test_digest.py
git commit -m "feat: technical alerts limited to one per 6 hours and the daily digest"
```

---

### Task 8: Команды бота

**Files:**
- Create: `src/homealize/notify/bot.py`
- Test: `tests/notify/test_bot.py` (новый)

**Interfaces:**
- Consumes:
  - `Notifier`, `outbox.pending_count` (Task 5);
  - `format.*` (Task 6);
  - `marks`, `apply_hidden`, `get_scored`, `load_scored` (Task 4);
  - `repo.last_run`, `repo.get_listing`, `repo.get_price_history`;
  - `TelegramError` (Task 5).
- Produces:
  - `homealize.notify.bot`:
    - `Reply(text, preview_url=None)`;
    - `answer(conn, settings, text, *, now, next_run=None) -> Reply` — чистый ответ на текст команды (отметки фиксирует сама);
    - `handle_update(conn, settings, notifier, update: dict, *, now, next_run=None) -> None` — проверяет чат, отвечает, ошибки только в лог;
    - `HELP`, `COMMANDS`.
  - Поддерживаются формы `/cmd арг`, `/cmd_арг` (кликабельные `/ad_123`), `/cmd@ИмяБота арг`; вместо id — ссылка на объявление.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/notify/test_bot.py`:

```python
from datetime import UTC, datetime, timedelta

from homealize.config import Settings
from homealize.models import RunSummary
from homealize.notify import bot
from homealize.notify.bot import answer, handle_update
from homealize.notify.outbox import Notifier
from homealize.storage import marks, repo
from homealize.storage.scores import replace_scores
from tests.factories import FakeTelegram, make_listing, score_record

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
SETTINGS = Settings()


def seed(db):
    """Три участка в рейтинге (третий — новый за сутки) и один снятый с сайта."""
    for lid, first_seen in (
        ("100000001", NOW - timedelta(days=3)),
        ("100000002", NOW - timedelta(days=3)),
        ("100000003", NOW - timedelta(hours=2)),
        ("100000004", NOW - timedelta(days=3)),
    ):
        repo.upsert_listing(db, make_listing(id=lid, url=f"https://999.md/ru/{lid}"), status="active",
                            filter_reason=None, now=first_seen)
    db.execute("UPDATE listings SET status = 'removed' WHERE id = '100000004'")
    replace_scores(db, [
        score_record("100000001", rank=1, total=80.0),
        score_record("100000002", rank=2, total=75.0),
        score_record("100000003", rank=3, total=70.0),
    ])
    db.commit()


def reply(db, text, **kwargs):
    return answer(db, SETTINGS, text, now=NOW, **kwargs)


def message(text, chat=42, update_id=1):
    return {"update_id": update_id, "message": {"chat": {"id": chat}, "text": text}}


def test_messages_from_other_chats_are_ignored(db):
    fake = FakeTelegram()
    handle_update(db, SETTINGS, Notifier(fake, 42), message("/top", chat=7), now=NOW)
    assert fake.sent == []


def test_reply_goes_to_the_own_chat(db):
    seed(db)
    fake = FakeTelegram()
    handle_update(db, SETTINGS, Notifier(fake, 42), message("/top"), now=NOW)
    assert fake.sent[0][0] == 42
    assert "<b>Топ-3</b>" in fake.sent[0][1]


def test_unknown_text_and_command_show_help(db):
    assert "/top [N]" in reply(db, "привет").text
    assert reply(db, "/foo").text.startswith("Не знаю команду /foo.")


def test_top_with_count_bot_name_and_limits(db):
    seed(db)
    assert reply(db, "/top 2").text.split("\n")[0] == "<b>Топ-2</b>"
    assert reply(db, "/top@HomealizeBot 1").text.split("\n")[1].startswith("1. 80 · Гратиешты")
    assert reply(db, "/top 0").text.split("\n")[0] == "<b>Топ-1</b>"
    assert "нужно число" in reply(db, "/top много").text


def test_top_when_the_rating_is_empty(db):
    assert "пусто" in reply(db, "/top").text


def test_ad_by_id_link_or_clickable_command(db):
    seed(db)
    for text in ("/ad 100000001", "/ad https://999.md/ru/100000001", "/ad_100000001"):
        result = reply(db, text)
        assert "Место в рейтинге: 1" in result.text
        assert result.preview_url == "https://i.simpalsmedia.com/999.md/BoardImages/900x900/aaa111.jpg"


def test_ad_for_unknown_and_unscored_listings(db):
    seed(db)
    assert reply(db, "/ad 123456789").text == "Объявления 123456789 нет в базе."
    assert "Укажите id" in reply(db, "/ad").text
    assert "не в оценке (снято с сайта)" in reply(db, "/ad 100000004").text


def test_hide_moves_the_listing_out_and_unhide_brings_it_back(db):
    seed(db)
    text = reply(db, "/hide 100000001").text
    assert text.startswith("Скрыто: Гратиешты, 6 сот. (/ad_100000001).")
    assert "/unhide_100000001" in text
    assert not db.in_transaction
    assert marks.marked(db, marks.HIDDEN) == {"100000001"}
    assert reply(db, "/top").text.split("\n")[1].startswith("1. 75")
    assert reply(db, "/hide 100000001").text.startswith("Уже скрыто")
    assert reply(db, "/unhide_100000001").text == (
        "Возвращено в рейтинг: Гратиешты, 6 сот. (/ad_100000001), место 1"
    )
    assert reply(db, "/unhide 100000001").text.startswith("Этот участок не был скрыт")


def test_favourites(db):
    seed(db)
    assert reply(db, "/fav 100000002").text.startswith("★ В избранном")
    assert reply(db, "/fav 100000002").text.startswith("Уже в избранном")
    assert "2. ★ 75" in reply(db, "/top").text
    assert reply(db, "/favs").text.split("\n")[0] == "<b>Избранное</b>: 1"
    assert reply(db, "/unfav 100000002").text.startswith("Убрано из избранного")
    assert "пока нет" in reply(db, "/favs").text


def test_new_lists_listings_of_the_last_day(db):
    seed(db)
    lines = reply(db, "/new").text.split("\n")
    assert lines[0] == "<b>Новые за сутки</b>: 1, в рейтинге 1"
    assert lines[1].startswith("3. 70")


def test_status_shows_the_last_run_with_short_errors(db):
    run_id = repo.start_run(db, datetime(2026, 10, 30, 5, 0, tzinfo=UTC))
    summary = RunSummary(search_complete=True, ads_seen=1780, new=3, errors=["x" * 300])
    repo.finish_run(db, run_id, summary, now=datetime(2026, 10, 30, 5, 1, 20, tzinfo=UTC))
    text = reply(db, "/status", next_run=datetime(2026, 10, 30, 8, 0, tzinfo=UTC)).text
    lines = text.split("\n")
    assert lines[0] == "<b>Последний прогон</b>: 30.10 07:00, ✅ успешен, 1 мин 20 с"
    assert lines[1].startswith("Увидено 1780, новых 3")
    assert lines[3] == "• " + "x" * 199 + "…"
    assert "Следующий прогон: 30.10 10:00" in text


def test_status_without_runs(db):
    assert reply(db, "/status").text == "Прогонов ещё не было."


def test_command_failure_is_answered_not_raised(db, monkeypatch):
    def broken(conn):
        raise RuntimeError("сломалось")

    monkeypatch.setattr(bot, "load_scored", broken)
    fake = FakeTelegram()
    handle_update(db, SETTINGS, Notifier(fake, 42), message("/top"), now=NOW)
    assert fake.texts() == ["Не получилось выполнить команду — подробности в logs/homealize.log."]
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `uv run pytest tests/notify/test_bot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'homealize.notify.bot'`.

- [ ] **Step 3: Реализовать `bot.py`**

Создать `src/homealize/notify/bot.py`:

```python
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
```

- [ ] **Step 4: Запустить тесты**

Run: `uv run pytest tests/notify/test_bot.py -q`
Expected: PASS.

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/homealize/notify/bot.py tests/notify/test_bot.py
git commit -m "feat: bot commands - top, new, card, favourites, hide and status"
```

---

### Task 9: Цикл прогона; CLI `run --dry-run`, `digest`, `telegram-test`; интеграционный тест

**Files:**
- Create: `src/homealize/jobs.py`
- Modify: `src/homealize/cli.py` (заменить целиком)
- Modify: `tests/test_cli.py` (новый `write_config`, цели `monkeypatch`, новые тесты)
- Create: `tests/test_jobs.py`

**Interfaces:**
- Consumes:
  - `run_once` и `RunSummary.failure` (Task 2);
  - `hold_lock`, `LockBusyError` (Task 2);
  - `repo.close_stale_runs`, `repo.mark_notified` (Tasks 2, 6);
  - `plan_after_run` (Task 6), `plan_alerts`, `Failure`, `plan_digest` (Task 7);
  - `enqueue`, `deliver`, `Notifier`, `Delivery`, `Outgoing` (Task 5);
  - `TelegramClient`, `TelegramError` (Task 5);
  - `load_telegram_secrets` (Task 1);
  - `evaluate`, `build_http_client`, `export_workbook`, `load_settlements` (этап 2).
- Produces:
  - `homealize.jobs`:
    - `Evaluation(summary, path, messages, failure)` и свойство `ok`;
    - `evaluate_and_export(conn, settings, *, now, http_factory=build_http_client, sleep=time.sleep) -> Evaluation`;
    - `CycleResult(scan, run_id, evaluation, planned, delivery, crash)`;
    - `run_cycle(conn, settings, *, source_factory, notifier, now, clock, dry_run=False, http_factory=build_http_client, sleep=time.sleep) -> CycleResult` — бросает `LockBusyError`;
    - `send_digest(conn, settings, notifier, *, now, dry_run=False, sleep=time.sleep) -> tuple[Outgoing, bool, Delivery | None]`.
  - CLI:
    - `run [--dry-run]`, `digest [--dry-run]`, `telegram-test`;
    - коды `SCAN_INTERRUPTED = 1`, `BAD_SETTINGS = 2`, `EVALUATION_FAILED = 3`, `BUSY = 4`;
    - `cli._telegram_client(settings, token)` — точка подмены клиента в тестах;
    - `cli._utc_now()`;
    - справочник сёл проверяется до `run` и `rescore` (код 2).

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_jobs.py` — интеграционный тест из спеки §9: полный цикл на поддельных 999.md, Overpass, BNM, OSRM и Telegram, повторный прогон без дублей.

```python
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from homealize import jobs
from homealize.config import Settings
from homealize.notify.outbox import Notifier
from homealize.runlock import LockBusyError, hold_lock
from homealize.sources.base import RateLimitedError
from homealize.storage import repo
from tests.factories import FakeSource, FakeTelegram, make_listing, page

PROJECT_ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)
OVERPASS = {"elements": [{"type": "node", "id": 1, "lat": 47.1, "lon": 28.82, "tags": {"shop": "supermarket"}}]}
BNM_XML = (
    '<?xml version="1.0" encoding="UTF-8"?><ValCurs Date="30.10.2026">'
    "<Valute ID=\"47\"><CharCode>EUR</CharCode><Nominal>1</Nominal><Value>20.1303</Value></Valute></ValCurs>"
)


def services_client() -> httpx.Client:
    """Поддельные Overpass, BNM и OSRM (10 минут, 8 км до любого въезда)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "overpass-api.de":
            return httpx.Response(200, json=OVERPASS)
        if request.url.host == "www.bnm.md":
            return httpx.Response(200, text=BNM_XML)
        if request.url.host == "router.project-osrm.org":
            n = len(request.url.params["sources"].split(";"))
            k = len(request.url.params["destinations"].split(";"))
            return httpx.Response(
                200, json={"code": "Ok", "durations": [[600.0] * k] * n, "distances": [[8000.0] * k] * n}
            )
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def settings(tmp_path):
    return Settings.model_validate({
        "storage": {"run_lock_path": str(tmp_path / "run.lock")},
        "scoring": {"settlements_path": str(PROJECT_ROOT / "config" / "settlements.yaml")},
        "export": {"dir": str(tmp_path / "exports")},
        "telegram": {"new_min_total": 0, "price_drop_min_total": 0},
    })


def plot(lid: str, **overrides):
    """Участки в 300 м друг от друга, чтобы не склеиться в дубликаты."""
    lat = 47.09 + int(lid) % 10 * 0.003
    return make_listing(id=lid, url=f"https://999.md/ru/{lid}", lat=lat, lon=28.82, **overrides)


def cycle(db, settings, *listings, now, telegram=None, dry_run=False, source=None):
    return jobs.run_cycle(
        db,
        settings,
        source_factory=lambda: source if source is not None else FakeSource([page(*listings)]),
        notifier=Notifier(telegram, 42) if telegram is not None else None,
        now=now,
        clock=lambda: now,
        dry_run=dry_run,
        http_factory=services_client,
        sleep=lambda _seconds: None,
    )


def test_cycle_scores_announces_and_does_not_repeat(db, settings):
    telegram = FakeTelegram()
    first = cycle(db, settings, plot("101"), plot("102"), plot("103"), now=T0, telegram=telegram)
    assert (first.scan.search_complete, first.evaluation.ok) == (True, True)
    assert len(telegram.sent) == 1 and "Первый прогон" in telegram.texts()[0]

    later = T0 + timedelta(hours=3)
    plots = [plot("101", price_original=25000.0), plot("102"), plot("103"), plot("104")]
    second = cycle(db, settings, *plots, now=later, telegram=telegram)
    texts = telegram.texts()[1:]
    assert len(texts) == 2
    assert any("Новый участок" in text and "/ad_104" in text for text in texts)
    assert any("Цена снижена" in text and "было 28 000 €, стало 25 000 € (−11%)" in text for text in texts)
    assert second.delivery.sent == 2
    assert repo.last_run(db)["notified_at"] == later.isoformat()

    cycle(db, settings, *plots, now=T0 + timedelta(hours=6), telegram=telegram)
    assert len(telegram.sent) == 3
    assert {row[0] for row in db.execute("SELECT status FROM notifications")} == {"sent"}


def test_failed_scan_sends_one_alert_per_six_hours(db, settings):
    telegram = FakeTelegram()
    cycle(db, settings, plot("101"), now=T0, telegram=telegram)
    for hours, expected in ((3, 2), (4, 2), (9, 3)):
        source = FakeSource([], fail_at=0, error=RateLimitedError("лимит"))
        result = cycle(db, settings, now=T0 + timedelta(hours=hours), telegram=telegram, source=source)
        assert result.scan.failure == "rate_limited"
        assert len(telegram.sent) == expected
    assert "ограничил частоту запросов" in telegram.texts()[1]


def test_dry_run_sends_nothing_and_keeps_the_window_open(db, settings):
    telegram = FakeTelegram()
    cycle(db, settings, plot("101"), now=T0, telegram=telegram)
    dry = cycle(db, settings, plot("101"), plot("102"), now=T0 + timedelta(hours=3), telegram=telegram, dry_run=True)
    assert [item.key for item in dry.planned] == ["new:102"]
    assert len(telegram.sent) == 1
    assert db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
    cycle(db, settings, plot("101"), plot("102"), now=T0 + timedelta(hours=6), telegram=telegram)
    assert "/ad_102" in telegram.texts()[-1]


def test_cycle_refuses_to_start_while_another_run_holds_the_lock(db, settings):
    with hold_lock(settings.storage.run_lock_path):
        with pytest.raises(LockBusyError):
            cycle(db, settings, plot("101"), now=T0)
    assert repo.last_run(db) is None


def test_stale_running_run_is_closed_first(db, settings):
    stale = repo.start_run(db, T0 - timedelta(hours=3))
    db.commit()
    cycle(db, settings, plot("101"), now=T0)
    assert db.execute("SELECT status FROM runs WHERE id = ?", (stale,)).fetchone()[0] == "failed"


def test_failed_evaluation_alerts_and_leaves_new_listings_for_later(db, settings, monkeypatch):
    telegram = FakeTelegram()
    cycle(db, settings, plot("101"), now=T0, telegram=telegram)
    real_evaluate = jobs.evaluate

    def broken(*args, **kwargs):
        raise RuntimeError("сломалось")

    monkeypatch.setattr(jobs, "evaluate", broken)
    result = cycle(db, settings, plot("101"), plot("102"), now=T0 + timedelta(hours=3), telegram=telegram)
    assert result.evaluation.failure.kind == "evaluation_failed"
    assert "оценка не выполнена" in telegram.texts()[-1]
    assert not any("Новый участок" in text for text in telegram.texts())
    assert repo.last_run(db)["notified_at"] is None

    monkeypatch.setattr(jobs, "evaluate", real_evaluate)
    cycle(db, settings, plot("101"), plot("102"), now=T0 + timedelta(hours=6), telegram=telegram)
    assert any("Новый участок" in text and "/ad_102" in text for text in telegram.texts())


def test_crash_before_the_scan_alerts(db, settings):
    telegram = FakeTelegram()

    def no_source():
        raise RuntimeError("нет сети")

    result = jobs.run_cycle(
        db, settings, source_factory=no_source, notifier=Notifier(telegram, 42), now=T0, clock=lambda: T0,
        http_factory=services_client, sleep=lambda _seconds: None,
    )
    assert (result.scan, result.run_id, result.crash) == (None, None, "RuntimeError: нет сети")
    assert telegram.texts()[0].startswith("🛠 <b>Тех. тревога</b>: прогон упал")
```

В `tests/test_cli.py`:

1. Импорты: `from homealize import cli` заменить на `from homealize import cli, jobs` и добавить:

```python
from homealize.runlock import hold_lock
from tests.factories import FakeSource, FakeTelegram, make_listing, page
```

(строку импорта `tests.factories` заменить этой).

2. `write_config` заменить на:

```python
def write_config(tmp_path: Path, *, env: str | None = None) -> Path:
    """Настройки теста: все пути во временной папке, .env — только если передан env."""
    data = tmp_path / "data"
    if env is not None:
        (tmp_path / ".env").write_text(env, encoding="utf-8")
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        f"storage:\n  db_path: '{(data / 'test.db').as_posix()}'\n"
        f"  run_lock_path: '{(data / 'run.lock').as_posix()}'\n"
        f"  serve_lock_path: '{(data / 'serve.lock').as_posix()}'\n"
        f"logging:\n  path: '{(tmp_path / 'logs' / 'test.log').as_posix()}'\n"
        f"export:\n  dir: '{(tmp_path / 'exports').as_posix()}'\n"
        f"telegram:\n  env_file: '{(tmp_path / '.env').as_posix()}'\n",
        encoding="utf-8",
    )
    return cfg
```

3. Оценка и выгрузка Excel для `run` и `rescore` теперь живут в `homealize.jobs`. Поэтому во всём файле:
   - `monkeypatch.setattr(cli, "evaluate", …)` заменить на `monkeypatch.setattr(jobs, "evaluate", …)`, в том числе в фикстуре `_fake_evaluation`;
   - в `test_run_returns_3_when_excel_fails_but_keeps_summary_and_warnings` заменить `monkeypatch.setattr(cli, "export_workbook", locked)` на `monkeypatch.setattr(jobs, "export_workbook", locked)`;
   - в `test_export_returns_3_when_excel_fails` (команда `export`) оставить `cli`.

4. В конец файла добавить:

```python
TELEGRAM_ENV = "TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=42\n"


def one_listing(monkeypatch):
    monkeypatch.setattr(cli, "build_nine99_source", lambda settings: FakeSource([page(make_listing(id="a"))]))


def test_run_without_telegram_says_so(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path)
    one_listing(monkeypatch)
    assert cli.main(["--config", str(cfg), "run"]) == 0
    assert "Telegram не настроен" in capsys.readouterr().out


def test_run_dry_run_prints_planned_messages(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    one_listing(monkeypatch)

    def no_client(settings, token):
        raise AssertionError("--dry-run не должен обращаться к Telegram")

    monkeypatch.setattr(cli, "_telegram_client", no_client)
    assert cli.main(["--config", str(cfg), "run", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "--dry-run: уведомлений к отправке 1" in out
    assert "Первый прогон: загружено 1 участок" in out
    assert "<b>" not in out


def test_run_sends_notifications_when_telegram_is_configured(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram()
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)
    one_listing(monkeypatch)
    assert cli.main(["--config", str(cfg), "run"]) == 0
    assert "Telegram: отправлено 1" in capsys.readouterr().out
    assert "Первый прогон" in fake.texts()[0]
    assert fake.closed


def test_run_is_refused_while_another_run_holds_the_lock(tmp_path, capsys):
    cfg = write_config(tmp_path)
    with hold_lock(tmp_path / "data" / "run.lock"):
        assert cli.main(["--config", str(cfg), "run"]) == 4
    assert "Другой прогон ещё идёт" in capsys.readouterr().err


def test_rescore_is_refused_while_a_run_holds_the_lock(tmp_path):
    cfg = write_config(tmp_path)
    with hold_lock(tmp_path / "data" / "run.lock"):
        assert cli.main(["--config", str(cfg), "rescore"]) == 4


def test_broken_settlements_file_is_a_settings_error(tmp_path, capsys):
    cfg = write_config(tmp_path)
    bad = tmp_path / "settlements.yaml"
    bad.write_text("- {id: 1}\n", encoding="utf-8")
    cfg.write_text(
        cfg.read_text(encoding="utf-8") + f"scoring:\n  settlements_path: '{bad.as_posix()}'\n", encoding="utf-8"
    )
    assert cli.main(["--config", str(cfg), "run"]) == 2
    assert "справочник сёл" in capsys.readouterr().err


def test_bad_env_file_is_a_settings_error(tmp_path):
    cfg = write_config(tmp_path, env="TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=abc\n")
    assert cli.main(["--config", str(cfg), "run"]) == 2


def test_digest_dry_run_prints_the_digest(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "digest", "--dry-run"]) == 0
    assert "Дайджест за" in capsys.readouterr().out


def test_digest_needs_telegram(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "digest"]) == 2
    assert "Telegram не настроен" in capsys.readouterr().err


def test_digest_is_sent_once_a_day(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram()
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)
    assert cli.main(["--config", str(cfg), "digest"]) == 0
    assert cli.main(["--config", str(cfg), "digest"]) == 0
    assert "уже отправлялся" in capsys.readouterr().out
    assert len(fake.sent) == 1


def test_telegram_test_without_a_token(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "telegram-test"]) == 2
    assert "@BotFather" in capsys.readouterr().err


def test_telegram_test_lists_chats_when_chat_id_is_missing(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env="TELEGRAM_BOT_TOKEN=t\n")
    update = {"update_id": 1, "message": {"chat": {"id": 555, "first_name": "Тест"}, "text": "привет"}}
    fake = FakeTelegram(updates=[[update]])
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)
    assert cli.main(["--config", str(cfg), "telegram-test"]) == 2
    out = capsys.readouterr().out
    assert "555 Тест" in out and "TELEGRAM_CHAT_ID" in out


def test_telegram_test_sends_a_message(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram()
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)
    assert cli.main(["--config", str(cfg), "telegram-test"]) == 0
    assert fake.sent == [(42, "✅ Homealize на связи. Команды: /help", None)]
    assert "@homealize_test_bot" in capsys.readouterr().out
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `uv run pytest tests/test_jobs.py tests/test_cli.py -q`
Expected: FAIL — нет модуля `homealize.jobs`, у `run` нет `--dry-run`, нет команд `digest` и `telegram-test`.

- [ ] **Step 3: `jobs.py`**

Создать `src/homealize/jobs.py`:

```python
"""Один цикл прогона (спека §4 «Поток одного прогона», §13): обход 999.md → оценка и Excel → уведомления.

Им пользуются и `homealize run`, и расписание `serve`. Весь цикл идёт под блокировкой прогона.
"""

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from homealize.config import Settings
from homealize.evaluate import build_http_client, evaluate
from homealize.export.excel import ExportError, export_workbook
from homealize.models import RunSummary
from homealize.notify.alerts import Failure, plan_alerts
from homealize.notify.digest import plan_digest
from homealize.notify.events import plan_after_run
from homealize.notify.outbox import Delivery, Notifier, Outgoing, deliver, enqueue
from homealize.pipeline import run_once
from homealize.runlock import hold_lock
from homealize.scoring.engine import ScoreSummary
from homealize.sources.base import Source
from homealize.storage import repo

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Evaluation:
    """Итог оценки и выгрузки: что удалось и что дописать в журнал прогона."""

    summary: ScoreSummary | None  # None — оценка упала
    path: Path | None  # None — Excel не сохранён
    messages: list[str]
    failure: Failure | None

    @property
    def ok(self) -> bool:
        return self.failure is None


@dataclass
class CycleResult:
    scan: RunSummary | None  # None — обход упал, не дойдя до итогов
    run_id: int | None  # None — прогон даже не начался
    evaluation: Evaluation | None  # None — оценка не запускалась
    planned: list[Outgoing] = field(default_factory=list)
    delivery: Delivery | None = None  # None — ничего не отправлялось (--dry-run или нет Telegram)
    crash: str | None = None


def evaluate_and_export(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    now: datetime,
    http_factory: Callable[[], httpx.Client] = build_http_client,
    sleep: Callable[[float], None] = time.sleep,
) -> Evaluation:
    """Оценка и Excel. Ошибки наружу не выходят: обход к этому моменту уже сохранён."""
    try:
        with http_factory() as http:
            result = evaluate(conn, settings, http, now=now, sleep=sleep)
        try:
            path = export_workbook(conn, settings.export, now=now)
        except ExportError as exc:
            message = f"Excel не сохранён: {exc}"
            return Evaluation(result, None, [*result.warnings, message], Failure("export_failed", message))
    except Exception as exc:  # оценка не должна терять уже сохранённый обход
        conn.rollback()  # недописанное оценкой не должно попасть в базу вместе с записью в журнал прогона
        log.exception("Оценка не выполнена")
        message = f"Оценка не выполнена: {type(exc).__name__}: {exc}"
        return Evaluation(None, None, [message], Failure("evaluation_failed", message))
    return Evaluation(result, path, list(result.warnings), None)


def run_cycle(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    source_factory: Callable[[], Source],
    notifier: Notifier | None,
    now: datetime,
    clock: Callable[[], datetime],
    dry_run: bool = False,
    http_factory: Callable[[], httpx.Client] = build_http_client,
    sleep: Callable[[float], None] = time.sleep,
) -> CycleResult:
    """Обход, оценка, Excel и уведомления. LockBusyError — если уже идёт другой прогон."""
    with hold_lock(settings.storage.run_lock_path):
        stale = repo.close_stale_runs(conn)
        conn.commit()
        if stale:
            log.warning("Закрыто зависших прогонов: %s", stale)
        before = repo.last_run(conn)
        scan, crash = _scan(conn, settings, source_factory, now=now, clock=clock)
        after = repo.last_run(conn)
        run_id = after["id"] if after is not None and (before is None or after["id"] != before["id"]) else None

        evaluation = None
        if crash is None:
            evaluation = evaluate_and_export(conn, settings, now=now, http_factory=http_factory, sleep=sleep)
            if run_id is not None and evaluation.messages:
                repo.add_run_errors(conn, run_id, evaluation.messages)
                conn.commit()

        planned = plan_alerts(conn, settings, _failures(scan, crash, evaluation), run_id=run_id, now=now)
        scored = run_id is not None and evaluation is not None and evaluation.summary is not None
        if scored:
            planned += plan_after_run(conn, settings, run_id=run_id, now=now)
        result = CycleResult(scan, run_id, evaluation, planned, crash=crash)
        if dry_run or notifier is None:
            return result  # окно «новых» остаётся открытым: они придут со следующим прогоном
        enqueue(conn, planned, now=now)
        if scored:
            repo.mark_notified(conn, run_id, now=now)
        conn.commit()
        result.delivery = deliver(conn, notifier, settings.telegram, now=now, sleep=sleep)
        return result


def send_digest(
    conn: sqlite3.Connection,
    settings: Settings,
    notifier: Notifier | None,
    *,
    now: datetime,
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Outgoing, bool, Delivery | None]:
    """Дайджест дня: сообщение, поставлено ли оно в очередь (False — за сегодня уже был) и итог отправки."""
    item = plan_digest(conn, settings, now=now)
    if dry_run or notifier is None:
        return item, False, None
    added = enqueue(conn, [item], now=now) > 0
    conn.commit()
    return item, added, deliver(conn, notifier, settings.telegram, now=now, sleep=sleep)


def _scan(
    conn: sqlite3.Connection,
    settings: Settings,
    source_factory: Callable[[], Source],
    *,
    now: datetime,
    clock: Callable[[], datetime],
) -> tuple[RunSummary | None, str | None]:
    """(итог обхода, None) или (None, текст сбоя), если упал сам обход или создание источника."""
    try:
        source = source_factory()
    except Exception as exc:
        log.exception("Источник объявлений не создан")
        return None, f"{type(exc).__name__}: {exc}"
    try:
        return run_once(conn, source, settings, now=now, clock=clock), None
    except Exception as exc:  # run_once уже записал прогон как failed и откатил недописанное
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        source.close()


def _failures(scan: RunSummary | None, crash: str | None, evaluation: Evaluation | None) -> list[Failure]:
    failures = []
    if crash is not None:
        failures.append(Failure("crashed", crash))
    elif scan is not None and scan.failure is not None:
        failures.append(Failure(scan.failure, scan.failure_detail))
    if evaluation is not None and evaluation.failure is not None:
        failures.append(evaluation.failure)
    return failures
```

- [ ] **Step 4: `cli.py`**

Заменить `src/homealize/cli.py` целиком:

```python
"""Командная строка: homealize init-db | run | rescore | export | refresh-poi | digest | telegram-test."""

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
from homealize.notify.outbox import Delivery, Notifier
from homealize.notify.telegram import TelegramClient, TelegramError
from homealize.runlock import LockBusyError, hold_lock
from homealize.settlements import load_settlements
from homealize.sources.nine99.source import build_nine99_source
from homealize.storage.db import connect, migrate

log = logging.getLogger(__name__)

DEFAULT_CONFIG = Path("config/settings.yaml")
SCAN_INTERRUPTED = 1
BAD_SETTINGS = 2
EVALUATION_FAILED = 3  # оценка, Excel, обновление данных или Telegram не удались
BUSY = 4
BUSY_MESSAGE = "Другой прогон ещё идёт (например, по расписанию serve) — попробуйте через пару минут."
NO_TELEGRAM = (
    "Telegram не настроен: нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env "
    "(см. README, раздел «Telegram», и `uv run homealize telegram-test`)."
)
NEEDS_SETTLEMENTS = {"run", "rescore"}


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
    return 0 if result.evaluation is not None and result.evaluation.ok else EVALUATION_FAILED


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
    conn = connect(settings.storage.db_path)
    try:
        migrate(conn)
        item, added, delivery = send_digest(conn, settings, notifier, now=_utc_now(), dry_run=dry_run)
    finally:
        conn.close()
        if notifier is not None:
            notifier.client.close()
    if dry_run:
        print(_plain(item.text))
        return 0
    if not added:
        print("Дайджест за сегодня уже отправлялся.")
        return 0
    _print_delivery(delivery)
    return 0 if delivery.failed == 0 else EVALUATION_FAILED


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
```

- [ ] **Step 5: Запустить тесты**

Run: `uv run pytest tests/test_jobs.py tests/test_cli.py -q`
Expected: PASS.

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/homealize/jobs.py src/homealize/cli.py tests/test_jobs.py tests/test_cli.py
git commit -m "feat: one run cycle for run and serve - scan, score, notify; run --dry-run, digest, telegram-test"
```

---

### Task 10: Расписание и `serve`

**Files:**
- Create: `src/homealize/scheduler.py`
- Create: `src/homealize/serve.py`
- Modify: `src/homealize/cli.py` (команда `serve`)
- Modify: `src/homealize/logging_setup.py` (без консоли под `pythonw`)
- Test: `tests/test_scheduler.py`, `tests/test_serve.py` (новые), `tests/test_cli.py`

**Interfaces:**
- Consumes:
  - `ScheduleSettings`, `parse_hhmm` (Task 1);
  - `send_digest`, `run_cycle` (Task 9);
  - `digest_key` (Task 7);
  - `handle_update` (Task 8);
  - `outbox.exists`, `outbox.pending_count`, `deliver`, `Notifier` (Task 5);
  - `LockBusyError`, `hold_lock` (Task 2).
- Produces:
  - `homealize.scheduler.Schedule(settings: ScheduleSettings)`. Все аргументы и результаты — aware `datetime`, сравнение в UTC:
    - `zone`;
    - `last_run_slot(now)`, `next_run_slot(now)`;
    - `run_due(now, last_started) -> bool`;
    - `digest_day(now) -> date`, `digest_at(day) -> datetime`;
    - `digest_due(now, *, sent_today) -> bool`;
    - `next_event(now) -> datetime`.
  - `homealize.serve.Service(conn, settings, notifier, *, run_job: Callable[[datetime], object], clock=_utc_now, sleep=time.sleep)` с методами `start()`, `tick()`, `run_forever()`.
  - CLI `serve`: код 2 без Telegram, 4 — если serve уже запущен, 0 — после Ctrl+C.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_scheduler.py`:

```python
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from homealize.config import ScheduleSettings
from homealize.scheduler import Schedule

ZONE = ZoneInfo("Europe/Chisinau")
SCHEDULE = Schedule(ScheduleSettings())


def at(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Время по Кишинёву в октябре 2026 (UTC+2), переведённое в UTC."""
    return datetime(2026, 10, day, hour, minute, second, tzinfo=ZONE).astimezone(UTC)


def test_last_and_next_run_slots():
    now = at(30, 11, 30)
    assert SCHEDULE.last_run_slot(now) == at(30, 10)
    assert SCHEDULE.next_run_slot(now) == at(30, 13)


def test_night_reaches_back_to_yesterday_and_forward_to_the_morning():
    now = at(30, 3)
    assert SCHEDULE.last_run_slot(now) == at(29, 22)
    assert SCHEDULE.next_run_slot(now) == at(30, 7)


def test_slot_time_itself_belongs_to_that_slot():
    assert SCHEDULE.last_run_slot(at(30, 13)) == at(30, 13)
    assert SCHEDULE.next_run_slot(at(30, 13)) == at(30, 16)


def test_run_is_due_on_time_and_not_twice():
    now = at(30, 10, 0, 20)
    assert SCHEDULE.run_due(now, last_started=at(30, 7))
    assert not SCHEDULE.run_due(now, last_started=at(30, 10, 0, 1))


def test_missed_runs_collapse_into_one_catch_up():
    now = at(30, 17, 40)  # ПК спал с утра, следующий прогон в 19:00
    assert SCHEDULE.run_due(now, last_started=at(30, 7))
    assert not SCHEDULE.run_due(now + timedelta(minutes=1), last_started=now)


def test_no_catch_up_right_before_the_next_slot():
    assert not SCHEDULE.run_due(at(30, 6, 30), last_started=at(29, 19))
    assert SCHEDULE.run_due(at(30, 7, 0, 5), last_started=at(29, 19))


def test_a_few_minutes_late_is_still_on_time():
    schedule = Schedule(ScheduleSettings(run_times=["07:00", "07:30"]))
    assert schedule.run_due(at(30, 7, 3), last_started=None)
    assert not schedule.run_due(at(30, 7, 10), last_started=None)  # догонять поздно: через 20 мин следующий


def test_first_ever_run_is_due():
    assert SCHEDULE.run_due(at(30, 11), last_started=None)


def test_digest_is_due_after_its_time_on_the_same_day_only():
    assert not SCHEDULE.digest_due(at(30, 19, 59), sent_today=False)
    assert SCHEDULE.digest_due(at(30, 23, 10), sent_today=False)
    assert not SCHEDULE.digest_due(at(30, 23, 10), sent_today=True)
    assert SCHEDULE.digest_day(at(31, 0, 30)) == date(2026, 10, 31)
    assert not SCHEDULE.digest_due(at(31, 8), sent_today=False)


def test_next_event_is_the_nearest_of_run_and_digest():
    assert SCHEDULE.next_event(at(30, 19, 30)) == at(30, 20)
    assert SCHEDULE.next_event(at(30, 20, 30)) == at(30, 22)
    assert SCHEDULE.next_event(at(30, 22, 30)) == at(31, 7)


def test_daylight_saving_change_keeps_local_times():
    summer = datetime(2027, 3, 28, 12, 0, tzinfo=UTC)  # 15:00 по Кишинёву, уже летнее время (UTC+3)
    assert SCHEDULE.last_run_slot(summer).astimezone(ZONE).hour == 13
    assert SCHEDULE.next_run_slot(summer).astimezone(ZONE).hour == 16
```

Создать `tests/test_serve.py`:

```python
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from homealize.config import Settings
from homealize.models import RunSummary
from homealize.notify import outbox
from homealize.notify.outbox import Notifier, Outgoing
from homealize.notify.telegram import TelegramError
from homealize.runlock import LockBusyError
from homealize.serve import Service
from homealize.storage import repo
from tests.factories import FakeTelegram

ZONE = ZoneInfo("Europe/Chisinau")
SETTINGS = Settings()


def at(day, hour, minute=0, second=0):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=ZONE).astimezone(UTC)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def service(db, now, *, telegram=None, run_job=None, sleeps=None):
    runs = []
    s = Service(
        db, SETTINGS, Notifier(telegram if telegram is not None else FakeTelegram(), 42),
        run_job=run_job or runs.append,
        clock=now if isinstance(now, Clock) else Clock(now),
        sleep=sleeps.append if sleeps is not None else (lambda _seconds: None),
    )
    return s, runs


def ran_at(db, moment):
    run_id = repo.start_run(db, moment)
    repo.finish_run(db, run_id, RunSummary(search_complete=True), now=moment)
    db.commit()


def test_scheduled_run_starts_on_time_and_only_once(db):
    ran_at(db, at(30, 7))
    s, runs = service(db, at(30, 10, 0, 10))
    s.tick()
    s.tick()
    assert runs == [at(30, 10, 0, 10)]


def test_missed_runs_collapse_into_one_catch_up_after_sleep(db):
    ran_at(db, at(30, 7))
    s, runs = service(db, at(30, 17, 40))
    s.tick()
    assert runs == [at(30, 17, 40)]


def test_no_catch_up_right_before_the_next_run(db):
    ran_at(db, at(29, 19))
    clock = Clock(at(30, 6, 30))
    s, runs = service(db, clock)
    s.tick()
    assert runs == []
    clock.now = at(30, 7, 0, 5)
    s.tick()
    assert runs == [at(30, 7, 0, 5)]


def test_poll_waits_until_the_next_event(db):
    ran_at(db, at(30, 10))
    telegram = FakeTelegram()
    service(db, at(30, 12, 59, 40), telegram=telegram)[0].tick()
    service(db, at(30, 11), telegram=telegram)[0].tick()
    assert telegram.polls == [(None, 20), (None, 50)]


def test_busy_or_crashing_run_does_not_stop_the_service(db):
    telegram = FakeTelegram()

    def busy(now):
        raise LockBusyError("run.lock")

    service(db, at(30, 10, 0, 10), telegram=telegram, run_job=busy)[0].tick()
    assert telegram.sent == []

    def boom(now):
        raise RuntimeError("сломалось")

    service(db, at(30, 13, 0, 10), telegram=telegram, run_job=boom)[0].tick()
    assert telegram.texts()[-1].startswith("🛠 <b>Тех. тревога</b>: прогон упал")
    assert "RuntimeError: сломалось" in telegram.texts()[-1]


def test_digest_is_sent_once_in_the_evening(db):
    ran_at(db, at(30, 19))
    telegram = FakeTelegram()
    s, runs = service(db, at(30, 20, 0, 30), telegram=telegram)
    s.tick()
    s.tick()
    assert len([text for text in telegram.texts() if "Дайджест за 30.10" in text]) == 1
    assert runs == []


def test_missed_digest_comes_the_same_night_but_not_next_morning(db):
    ran_at(db, at(30, 22))
    late = FakeTelegram()
    service(db, at(30, 23, 30), telegram=late)[0].tick()
    assert any("Дайджест за 30.10" in text for text in late.texts())
    ran_at(db, at(31, 7))
    morning = FakeTelegram()
    service(db, at(31, 8), telegram=morning)[0].tick()
    assert not any("Дайджест" in text for text in morning.texts())


def test_commands_are_answered_and_the_offset_moves_on(db):
    ran_at(db, at(30, 10))
    update = {"update_id": 5, "message": {"chat": {"id": 42}, "text": "/status"}}
    telegram = FakeTelegram(updates=[[update]])
    s, _ = service(db, at(30, 11), telegram=telegram)
    s.tick()
    s.tick()
    assert telegram.texts()[0].startswith("<b>Последний прогон</b>: 30.10 10:00")
    assert "Следующий прогон: 30.10 13:00" in telegram.texts()[0]
    assert [offset for offset, _timeout in telegram.polls] == [None, 6]


def test_poll_failures_back_off(db):
    ran_at(db, at(30, 10))
    telegram = FakeTelegram(updates=[TelegramError("нет сети"), TelegramError("нет сети"), []])
    sleeps = []
    s, _ = service(db, at(30, 11), telegram=telegram, sleeps=sleeps)
    for _ in range(3):
        s.tick()
    assert sleeps == [5, 15]


def test_pending_notifications_are_retried_between_runs(db):
    ran_at(db, at(30, 10))
    outbox.enqueue(db, [Outgoing("alert:x:1", "alert", "тревога", topic="x")], now=at(30, 10, 5))
    db.commit()
    telegram = FakeTelegram()
    service(db, at(30, 10, 30), telegram=telegram)[0].tick()
    assert telegram.texts() == ["тревога"]


def test_start_message(db):
    telegram = FakeTelegram()
    service(db, at(30, 11), telegram=telegram)[0].start()
    assert telegram.texts() == [
        "✅ Homealize запущен. Прогоны: 07:00, 10:00, 13:00, 16:00, 19:00, 22:00, дайджест в 20:00. Команды: /help"
    ]
```

Добавить в `tests/test_cli.py` (импорт `import sys` и `from homealize.config import LoggingSettings` — к существующим):

```python
def test_serve_needs_telegram(tmp_path, capsys):
    cfg = write_config(tmp_path)
    assert cli.main(["--config", str(cfg), "serve"]) == 2
    assert "Для serve нужен Telegram" in capsys.readouterr().err


def test_second_serve_is_refused(tmp_path, capsys, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: FakeTelegram())
    with hold_lock(tmp_path / "data" / "serve.lock"):
        assert cli.main(["--config", str(cfg), "serve"]) == 4
    assert "serve уже запущен" in capsys.readouterr().err


def test_serve_runs_until_interrupted(tmp_path, monkeypatch):
    cfg = write_config(tmp_path, env=TELEGRAM_ENV)
    fake = FakeTelegram()
    monkeypatch.setattr(cli, "_telegram_client", lambda settings, token: fake)

    def stop(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.Service, "run_forever", stop)
    assert cli.main(["--config", str(cfg), "serve"]) == 0
    assert fake.closed


def test_logging_without_a_console(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stderr", None)  # так запускает pythonw (автозапуск)
    configure_logging(LoggingSettings(path=tmp_path / "x.log"))
    logging.getLogger("homealize.test").warning("без консоли")
    reset_logging()
    assert "без консоли" in (tmp_path / "x.log").read_text(encoding="utf-8")
```

- [ ] **Step 2: Запустить — тесты падают**

Run: `uv run pytest tests/test_scheduler.py tests/test_serve.py tests/test_cli.py -q`
Expected: FAIL — нет модулей `homealize.scheduler`, `homealize.serve`, нет команды `serve`; `configure_logging` при `sys.stderr = None` добавляет консольный обработчик.

- [ ] **Step 3: `scheduler.py`**

Создать `src/homealize/scheduler.py`:

```python
"""Когда пора делать прогон и дайджест (спека §4 «Расписание», §13). Время по местному часовому поясу.

Пропущенные прогоны (ПК спал или был выключен) схлопываются в один догоняющий; его не делают, если
следующий по расписанию ближе catch_up_gap_minutes. Пропущенный дайджест отправляется до конца того же дня.
"""

from datetime import UTC, date, datetime, time, timedelta

from homealize.config import ScheduleSettings, parse_hhmm

DAY = timedelta(days=1)
ON_TIME = timedelta(minutes=5)  # прогон, опоздавший не больше, считается сделанным вовремя


class Schedule:
    def __init__(self, settings: ScheduleSettings) -> None:
        self.zone = settings.zone
        self._run_times = [parse_hhmm(value) for value in settings.run_times]
        self._digest_time = parse_hhmm(settings.digest_time)
        self._gap = timedelta(minutes=settings.catch_up_gap_minutes)

    def last_run_slot(self, now: datetime) -> datetime:
        """Последнее время прогона по расписанию, не позже now."""
        today = self.digest_day(now)
        return max(slot for day in (today - DAY, today) for slot in self._slots(day, self._run_times) if slot <= now)

    def next_run_slot(self, now: datetime) -> datetime:
        """Ближайшее время прогона по расписанию позже now."""
        today = self.digest_day(now)
        return min(slot for day in (today, today + DAY) for slot in self._slots(day, self._run_times) if slot > now)

    def run_due(self, now: datetime, last_started: datetime | None) -> bool:
        slot = self.last_run_slot(now)
        if last_started is not None and last_started >= slot:
            return False
        if now - slot <= ON_TIME:
            return True
        return self.next_run_slot(now) - now >= self._gap  # догоняющий — только если до следующего далеко

    def digest_day(self, now: datetime) -> date:
        """Местная дата: по ней называется дайджест."""
        return now.astimezone(self.zone).date()

    def digest_at(self, day: date) -> datetime:
        return self._slots(day, [self._digest_time])[0]

    def digest_due(self, now: datetime, *, sent_today: bool) -> bool:
        return not sent_today and now >= self.digest_at(self.digest_day(now))

    def next_event(self, now: datetime) -> datetime:
        """Ближайшее будущее дело: прогон или дайджест."""
        today = self.digest_day(now)
        digests = [moment for moment in (self.digest_at(today), self.digest_at(today + DAY)) if moment > now]
        return min([self.next_run_slot(now), *digests])

    def _slots(self, day: date, times: list[time]) -> list[datetime]:
        return [datetime.combine(day, moment, tzinfo=self.zone).astimezone(UTC) for moment in times]
```

- [ ] **Step 4: `serve.py`**

Создать `src/homealize/serve.py`:

```python
"""`homealize serve`: прогоны по расписанию и команды бота в одном процессе (спека §4, §13).

Один поток: сделать просроченное (прогон, дайджест), затем ждать сообщений боту long polling до
следующего дела. Во время прогона (1–2 минуты) команды ждут и выполняются после него.
"""

import logging
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from html import escape

from homealize.config import Settings
from homealize.jobs import send_digest
from homealize.notify import outbox
from homealize.notify.bot import handle_update
from homealize.notify.digest import digest_key
from homealize.notify.format import short
from homealize.notify.outbox import Notifier, deliver
from homealize.notify.telegram import TelegramError
from homealize.runlock import LockBusyError
from homealize.scheduler import Schedule
from homealize.storage import repo

log = logging.getLogger(__name__)

POLL_RETRY_SECONDS = (5, 15, 60)  # пауза после неудачного опроса Telegram: первая, вторая, дальше
RETRY_PENDING = timedelta(minutes=15)  # как часто досылать очередь между прогонами


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Service:
    def __init__(
        self,
        conn: sqlite3.Connection,
        settings: Settings,
        notifier: Notifier,
        *,
        run_job: Callable[[datetime], object],
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._conn = conn
        self._settings = settings
        self._notifier = notifier
        self._schedule = Schedule(settings.schedule)
        self._run_job = run_job
        self._clock = clock
        self._sleep = sleep
        self._offset: int | None = None
        self._handled_slot: datetime | None = None  # слот, по которому прогон уже запускали (или пропустили)
        self._digest_tried: date | None = None
        self._last_retry: datetime | None = None
        self._poll_failures = 0

    def start(self) -> None:
        """Сообщение о запуске: по нему видно, что автозапуск сработал."""
        schedule = self._settings.schedule
        self._send(
            f"✅ Homealize запущен. Прогоны: {', '.join(schedule.run_times)}, дайджест в {schedule.digest_time}. "
            "Команды: /help"
        )

    def run_forever(self) -> None:
        self.start()
        while True:
            self.tick()

    def tick(self) -> None:
        """Одна итерация: просроченный прогон, дайджест, досылка очереди, ожидание команд."""
        now = self._clock()
        slot = self._schedule.last_run_slot(now)
        if slot != self._handled_slot and self._schedule.run_due(now, self._last_started()):
            self._handled_slot = slot
            self._do_run(now)
        now = self._clock()
        day = self._schedule.digest_day(now)
        sent_today = outbox.exists(self._conn, digest_key(day))
        if day != self._digest_tried and self._schedule.digest_due(now, sent_today=sent_today):
            self._digest_tried = day
            self._do_digest(now)
        self._retry_pending(self._clock())
        self._poll()

    def _do_run(self, now: datetime) -> None:
        try:
            self._run_job(now)
        except LockBusyError:
            log.info("Прогон по расписанию пропущен: идёт другой прогон")
        except Exception as exc:
            self._conn.rollback()
            log.exception("Прогон по расписанию упал")
            self._send(f"🛠 <b>Тех. тревога</b>: прогон упал\n{escape(short(f'{type(exc).__name__}: {exc}'))}")

    def _do_digest(self, now: datetime) -> None:
        try:
            send_digest(self._conn, self._settings, self._notifier, now=now, sleep=self._sleep)
        except Exception:
            self._conn.rollback()
            log.exception("Дайджест не отправлен")

    def _retry_pending(self, now: datetime) -> None:
        if self._last_retry is not None and now - self._last_retry < RETRY_PENDING:
            return
        self._last_retry = now
        if not outbox.pending_count(self._conn):
            return
        try:
            deliver(self._conn, self._notifier, self._settings.telegram, now=now, sleep=self._sleep)
        except Exception:
            self._conn.rollback()
            log.exception("Очередь уведомлений не отправлена")

    def _poll(self) -> None:
        now = self._clock()
        wait = (self._schedule.next_event(now) - now).total_seconds()
        timeout = int(max(1, min(self._settings.telegram.poll_timeout_seconds, wait)))
        try:
            updates = self._notifier.client.get_updates(self._offset, timeout)
        except TelegramError as exc:
            delay = POLL_RETRY_SECONDS[min(self._poll_failures, len(POLL_RETRY_SECONDS) - 1)]
            self._poll_failures += 1
            log.warning("Telegram: %s — повтор через %s с", exc, delay)
            self._sleep(delay)
            return
        self._poll_failures = 0
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._offset = update_id + 1
            try:
                moment = self._clock()
                handle_update(self._conn, self._settings, self._notifier, update, now=moment,
                              next_run=self._schedule.next_run_slot(moment))
            except Exception:
                self._conn.rollback()
                log.exception("Сообщение боту не обработано")

    def _last_started(self) -> datetime | None:
        run = repo.last_run(self._conn)
        return datetime.fromisoformat(run["started_at"]) if run is not None else None

    def _send(self, text: str) -> None:
        try:
            self._notifier.client.send_message(self._notifier.chat_id, text)
        except TelegramError as exc:
            log.warning("Сообщение не отправлено: %s", exc)
```

- [ ] **Step 5: CLI `serve` и логи без консоли**

В `src/homealize/cli.py`:

1. Первая строка докстроки: `"""Командная строка: homealize init-db | run | rescore | export | refresh-poi | serve | digest | telegram-test."""`.
2. Добавить импорт `from homealize.serve import Service`.
3. `NEEDS_SETTLEMENTS = {"run", "rescore", "serve"}`.
4. После `commands.add_parser("refresh-poi", …)` добавить:

```python
    commands.add_parser("serve", help="прогоны по расписанию и Telegram-бот (работает, пока не остановят)")
```

5. В `handlers` добавить `"serve": lambda: _serve(settings),`.
6. После `_refresh_poi` добавить:

```python
def _serve(settings: Settings) -> int:
    configure_logging(settings.logging)
    try:
        secrets = load_telegram_secrets(settings.telegram.env_file)
    except ValueError as exc:
        print(f"Не удалось прочитать {settings.telegram.env_file}: {exc}", file=sys.stderr)
        return BAD_SETTINGS
    if secrets is None or secrets.chat_id is None:
        print(f"Для serve нужен Telegram. {NO_TELEGRAM}", file=sys.stderr)
        return BAD_SETTINGS
    try:
        with hold_lock(settings.storage.serve_lock_path):
            return _serve_locked(settings, Notifier(_telegram_client(settings, secrets.token), secrets.chat_id))
    except LockBusyError:
        print("serve уже запущен (например, автозапуском) — второй не нужен.", file=sys.stderr)
        return BUSY


def _serve_locked(settings: Settings, notifier: Notifier) -> int:
    conn = connect(settings.storage.db_path)
    try:
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
    finally:
        conn.close()
        notifier.client.close()
    return 0
```

В `src/homealize/logging_setup.py` добавить `import sys` и заменить тело `configure_logging` на:

```python
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
```

- [ ] **Step 6: Запустить тесты**

Run: `uv run pytest tests/test_scheduler.py tests/test_serve.py tests/test_cli.py -q`
Expected: PASS.

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/homealize/scheduler.py src/homealize/serve.py src/homealize/cli.py src/homealize/logging_setup.py tests/test_scheduler.py tests/test_serve.py tests/test_cli.py
git commit -m "feat: homealize serve - schedule with catch-up runs, daily digest and bot polling"
```

---

### Task 11: Автозапуск, README, спека §13, живая проверка

**Files:**
- Create: `scripts/autostart.ps1` (UTF-8 с BOM — иначе Windows PowerShell 5.1 исказит кириллицу)
- Modify: `README.md` (заменить целиком)
- Modify: `docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md` (добавить §13 в конец)

**Interfaces:**
- Consumes: CLI `serve`, `run --dry-run`, `digest --dry-run`, `telegram-test` (Tasks 9–10).
- Produces:
  - `scripts/autostart.ps1 -Install | -Remove`;
  - документация для пользователя;
  - §13 спеки.

- [ ] **Step 1: `scripts/autostart.ps1`**

Создать `scripts/autostart.ps1`:

```powershell
<#
.SYNOPSIS
  Автозапуск Homealize при входе в Windows: задача Планировщика «Homealize» запускает `homealize serve` без окна.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Install
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Remove
#>
param(
    [switch]$Install,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$TaskName = "Homealize"
$Root = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$User = "$env:USERDOMAIN\$env:USERNAME"

if ($Install -eq $Remove) {
    Write-Host "Укажите -Install (включить автозапуск) или -Remove (выключить)."
    exit 2
}

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Задача «$TaskName» удалена, serve остановлен."
    } else {
        Write-Host "Задачи «$TaskName» нет — удалять нечего."
    }
    exit 0
}

if (-not (Test-Path $Pythonw)) {
    Write-Host "Не найден $Pythonw — выполните в корне проекта: uv sync"
    exit 1
}
if (-not (Test-Path (Join-Path $Root ".env"))) {
    Write-Host "Нет файла .env с TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID — см. README, раздел «Telegram»."
    exit 1
}

$action = New-ScheduledTaskAction -Execute $Pythonw -Argument "-m homealize.cli serve" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$options = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $options `
    -Principal $principal -Description "Homealize: прогоны 999.md по расписанию и Telegram-бот" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Готово: serve запущен и будет стартовать при каждом входе в Windows."
Write-Host "Бот пришлёт «Homealize запущен». Логи: $Root\logs\homealize.log"
```

Пересохранить файл в UTF-8 с BOM:

Run: `uv run python -c "import pathlib; p = pathlib.Path('scripts/autostart.ps1'); p.write_text(p.read_text(encoding='utf-8'), encoding='utf-8-sig')"`

Проверить, что скрипт разбирается и без параметров ничего не делает:

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\autostart.ps1; echo "exit=$LASTEXITCODE"`
Expected: «Укажите -Install (включить автозапуск) или -Remove (выключить).» и `exit=2`. **С `-Install` и `-Remove` скрипт не запускать:** задачу регистрирует пользователь сам.

- [ ] **Step 2: README**

Заменить `README.md` целиком:

````markdown
# Homealize

Мониторинг участков под строительство дома на 999.md. Программа собирает объявления и историю цен, оценивает участки по «весам», выгружает рейтинг в Excel и по расписанию присылает в Telegram новые участки, снижения цен и дайджест.

Спецификация: `docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md`.

## Быстрый старт

```bash
uv sync
uv run homealize init-db
uv run homealize run
```

`run` делает три шага:

1. Обходит поиск 999.md: около 23 запросов с паузами 2–3 с, примерно минута. Сохраняет объявления.
2. Оценивает активные участки и пишет рейтинг в `exports/latest.xlsx`, плюс копию за день `exports/homealize_ГГГГ-ММ-ДД.xlsx`. Копии хранятся 30 дней.
3. Если настроен Telegram (см. ниже), присылает новые подходящие участки, снижения цен и технические тревоги.

Для оценки программа обращается к внешним сервисам:

- **OpenStreetMap (Overpass)**: магазины, школы, детсады, аптеки, медпункты, остановки. Данные скачиваются раз в 30 дней, запрос занимает около минуты.
- **OSRM**: время и расстояние по дорогам до ближайшего из 14 въездов в Кишинёв. Считаются один раз для каждой точки и запоминаются.
- **Нацбанк Молдовы**: курс для цен в леях и долларах, раз в день.

Если сервис недоступен, используются сохранённые данные. В выводе и в журнале прогонов (`runs.errors`, команда бота `/status`) появляется «Предупреждение: …».

При первом запуске сохранённых данных ещё нет, поэтому инфраструктура не оценивается. Выполните `uv run homealize refresh-poi`, затем `uv run homealize rescore`.

- Настройки: `config/settings.yaml` (веса, бюджет, пороги, точки въезда, расписание, пороги уведомлений).
- Справочник сёл: `config/settlements.yaml`.
- База: `data/homealize.db`. Логи: `logs/homealize.log`.

Маршруты запоминаются навсегда по точке участка. После правки `routing.entry_points` или `routing.osrm_url` очистите кэш маршрутов и пересчитайте оценку:

```bash
uv run python -c "import sqlite3; c = sqlite3.connect('data/homealize.db'); c.execute('DELETE FROM route_cache'); c.commit()"
uv run homealize rescore
```

## Telegram

1. В Telegram откройте @BotFather, отправьте `/newbot` и придумайте боту имя. BotFather пришлёт токен.
2. Скопируйте `.env.example` в `.env` и впишите токен: `TELEGRAM_BOT_TOKEN=…`. Файл `.env` в git не попадает. Токен никому не показывайте: с ним можно писать от имени бота.
3. Напишите своему боту любое сообщение и выполните `uv run homealize telegram-test`. Команда покажет ваш chat id — впишите его в `.env`: `TELEGRAM_CHAT_ID=…`.
4. Ещё раз выполните `uv run homealize telegram-test`: бот пришлёт «Homealize на связи».

Что приходит:

- 🆕 **новый участок** — балл не ниже 70 или место в топ-10, не больше 10 за прогон (остальные — одной строкой);
- 📉 **снижение цены** — хотя бы на 5%, если балл не ниже 60;
- 📋 **дайджест в 20:00** — новые за сутки, топ-10 со сдвигами мест, кто выпал из топа и почему;
- 🛠 **тех. тревога** — обход, оценка или Excel не удались; одна и та же — не чаще раза в 6 часов.

Пороги задаются в секции `telegram` файла `config/settings.yaml`. Если Telegram недоступен, сообщения ждут в очереди и уходят позже. Неотправленные за сутки отбрасываются.

`uv run homealize run --dry-run` и `uv run homealize digest --dry-run` показывают сообщения в консоли, ничего не отправляя.

Команды бота:

| Команда | Что делает |
|---|---|
| `/top [N]` | лучшие N участков, по умолчанию 10 |
| `/new` | новые за сутки |
| `/ad <id>` | карточка: разбивка балла с пояснениями, метки, история цены, ссылки. Вместо id можно прислать ссылку на объявление; в списках под каждым участком есть кликабельная `/ad_<id>` |
| `/fav <id>`, `/unfav <id>`, `/favs` | избранное ★ |
| `/hide <id>`, `/unhide <id>` | убрать участок из рейтинга, уведомлений и дайджеста — и вернуть |
| `/status` | последний прогон, его ошибки и предупреждения, следующий прогон |

## Расписание и автозапуск

`uv run homealize serve` делает прогоны в 07:00, 10:00, 13:00, 16:00, 19:00, 22:00 и присылает дайджест в 20:00 по Кишинёву. Кроме того, бот отвечает на команды. serve работает, пока его не остановят (Ctrl+C). Время задаётся в секции `schedule` настроек.

Если ПК спал или был выключен, после пробуждения делается один догоняющий прогон, если до следующего по расписанию больше часа. Пропущенный дайджест приходит до полуночи того же дня. Пока идёт прогон (1–2 минуты), бот отвечает с задержкой.

Автозапуск при входе в Windows (без окна):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Install
```

Скрипт регистрирует задачу Планировщика «Homealize» и сразу её запускает, а бот присылает «Homealize запущен». Убрать автозапуск и остановить serve:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Remove
```

Если Windows ответит «Отказано в доступе», запустите PowerShell от имени администратора и повторите.

Два serve одновременно не запустятся. Ручной `run` во время прогона по расписанию не начнётся и выйдет с кодом 4.

## Команды

| Команда | Что делает |
|---|---|
| `init-db` | создаёт или обновляет базу |
| `run [--dry-run]` | обход сайта, оценка, Excel, уведомления; `--dry-run` показывает уведомления вместо отправки |
| `rescore` | оценка и Excel без обхода сайта, например после правки весов |
| `export` | только Excel из уже посчитанных баллов |
| `refresh-poi` | только заново скачивает инфраструктуру из OpenStreetMap; чтобы обновить баллы и Excel, после неё выполните `rescore` |
| `serve` | прогоны по расписанию, дайджест и бот |
| `digest [--dry-run]` | дайджест дня прямо сейчас (в Telegram — не больше одного в день) |
| `telegram-test` | проверить бота и узнать chat id |

Команды нужно запускать из корня проекта: пути к настройкам, базе, логам и Excel относительные. Другой файл настроек задаётся параметром `--config PATH` перед командой:

```bash
uv run homealize --config путь/к/settings.yaml run
```

Коды выхода:

- `0` — всё выполнено;
- `1` — обход прерван или ему нельзя доверять: ошибка сети, лимит запросов, неожиданный ответ сайта, слишком много нераспознанных объявлений. Сохранённые страницы остаются, пропавшие объявления не снимаются;
- `2` — не удалось прочитать настройки, `.env` или справочник сёл, либо для `serve` или `digest` не настроен Telegram;
- `3` — не удались оценка, сохранение Excel, обновление данных (`refresh-poi`) или Telegram. Обход, сделанный `run`, при этом сохранён;
- `4` — уже идёт другой прогон, или serve уже запущен.

Если `latest.xlsx` открыт в Excel, сохранить его нельзя: закройте файл и выполните `uv run homealize export`.

## Если сузить фильтр площади

Объявления вне нового диапазона поиск больше не вернёт, но в базе они останутся «известными». Если их окажется больше половины, каждый прогон будет считаться ненадёжным: снятие пропущено, приходит тревога «обход увидел слишком мало объявлений».

После правки `filters.area_sot_min` / `area_sot_max` один раз снимите такие объявления вручную, подставив свои числа вместо 5 и 10:

```bash
uv run python -c "import sqlite3; c = sqlite3.connect('data/homealize.db'); c.execute('UPDATE listings SET status = ? WHERE status != ? AND (area_sot < 5 OR area_sot > 10)', ('removed', 'removed')); c.commit()"
```

## Тесты

```bash
uv run pytest
uv run pytest -m live
```

Первая команда запускает офлайн-тесты; к Telegram они не обращаются. Вторая делает по одному настоящему запросу к 999.md, OSRM и Нацбанку и небольшой запрос к Overpass, проверяя, что форматы ответов не изменились.
````

- [ ] **Step 3: Спека §13**

Добавить в конец `docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md`:

```markdown

## 13. Уточнения при планировании этапа 3 (2026-10-30)

Этот раздел уточняет разделы 4, 5, 7, 8 и 10; где он расходится с ними, действует он.

- **Telegram без библиотек.** Bot API вызывается напрямую через httpx (`sendMessage`, `getUpdates`, `getMe`), python-telegram-bot не используется. Фото объявления показывается превью ссылки над текстом. Длинные ответы делятся на части по 4000 символов.
- **Расписание без APScheduler.** `serve` работает в одном потоке:
  - делает просроченное (прогон, дайджест), а между делами ждёт команды бота (long polling);
  - во время прогона команды выполняются после него;
  - POI и курс обновляются внутри прогона по своим кэшам (30 дней и день).
- **Сон и выключенный ПК.**
  - Пропущенные прогоны схлопываются в один догоняющий. Его не делают, если до следующего по расписанию меньше `schedule.catch_up_gap_minutes` (60).
  - Прогон с опозданием до 5 минут считается сделанным вовремя.
  - Пропущенный дайджест отправляется до конца того же дня по Кишинёву.
- **Автозапуск на ПК.** Задача Планировщика Windows «Homealize» срабатывает при входе пользователя (`scripts/autostart.ps1 -Install`, `-Remove`): `pythonw` без окна, без ограничения времени.
- **Очередь уведомлений** — таблица `notifications`:
  - поля:
    - `key` (уникален);
    - `kind` (`new`, `price_drop`, `first_run`, `digest`, `alert`);
    - `topic` (вид тревоги или дата дайджеста), `listing_id`;
    - `payload` (текст, фото, топ дайджеста);
    - `status` (`pending`, `sent`, `dropped`), `attempts`, `last_error`, `created_at`, `sent_at`;
  - неотправленное уходит при следующей отправке: после прогона, после дайджеста или раз в 15 минут в serve;
  - после 3 неудачных попыток, ошибки HTTP 400 или через сутки сообщение получает статус `dropped`.
- **Окно «новых».**
  - `runs.notified_at` отмечает прогоны, по которым уже готовились уведомления.
  - Новые объявления и снижения цен берутся с первого прогона после последнего отмеченного, но не раньше чем сутки назад.
  - Прогон с упавшей оценкой, `--dry-run` или прогон без Telegram окно не закрывают.
- **Новый участок** отвечает всем условиям:
  - впервые увиден в окне;
  - в рейтинге;
  - `total ≥ 70` или `rank ≤ 10`;
  - в его группе дубликатов нет активных объявлений, увиденных раньше окна.

  Отправляется не больше 10 за прогон, остальные — одной строкой «ещё N — /new».
- **Первый прогон.** Если в базе нет объявлений, увиденных раньше начала окна, вместо новых и снижений цен приходит одна сводка.
- **Снижение цены.** Последняя запись `price_history` сделана в окне, а предыдущая цена выше хотя бы на 5%. В одной валюте сравниваются исходные цены, в разных — цены в евро по последнему курсу BNM. Объявление должно быть в рейтинге, `total ≥ 60`.
- **Скрытые и избранные** хранятся в `user_marks` (`fav`, `hidden`).
  - Скрытое объявление получает `scores.excluded_reason = hidden`, если других причин нет, и не попадает в рейтинг, уведомления и дайджест.
  - Кроме команд из §7.1 есть `/unfav` и `/favs`.
- **Тех. тревоги.**
  - Виды:
    - `rate_limited`;
    - `empty_search`;
    - `source_unavailable` (в том числе HTTP 401/403 и временные ошибки GraphQL);
    - `source_schema`;
    - `unparsed`;
    - `too_few_seen`;
    - `crashed`;
    - `evaluation_failed`;
    - `export_failed`.
  - Тревога одного вида — не чаще `telegram.alert_repeat_hours` (6 ч).
  - Предупреждения Overpass, OSRM и BNM тревогой не считаются: их видно в `/status`.
- **Блокировки.**
  - На время прогона и `rescore` блокируется файл `data/run.lock`, а `serve` держит `data/serve.lock`.
  - Под блокировкой прогоны в статусе `running` закрываются как `failed`.
  - `finished_at` — настоящее время окончания прогона.
- **Секреты.** `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` лежат в `.env`, переменные окружения важнее файла. Токен не попадает в логи, базу и вывод.
- **Команды CLI:** `run [--dry-run]`, `serve`, `digest [--dry-run]`, `telegram-test`.
- **Коды выхода:**
  - 0 — всё выполнено;
  - 1 — обход прерван;
  - 2 — настройки не прочитаны (в том числе `.env`, справочник сёл, нет Telegram для `serve` и `digest`);
  - 3 — оценка, Excel, обновление данных или Telegram не удались;
  - 4 — уже идёт другой прогон или serve уже запущен.
- **Обход 999.md.** Пустая последняя страница при уменьшившемся `count` завершает проход. `mark_unseen` считает снятыми только бывшие активные объявления.
```

- [ ] **Step 4: Полный прогон тестов**

Run: `uv run pytest -q`
Expected: PASS (все офлайн-тесты).

Run: `uv run pytest -m live -q`
Expected: PASS (по одному настоящему запросу к 999.md, OSRM, BNM и Overpass). Если сервис временно недоступен — записать в отчёт, не чинить.

- [ ] **Step 5: Живая проверка на копии базы**

Настоящая база не трогается: всё делается на её копии во временной папке. Команды для PowerShell из корня проекта:

```powershell
$env:PYTHONIOENCODING = "utf-8"
$live = Join-Path $env:TEMP "homealize-live"
New-Item -ItemType Directory -Force $live | Out-Null
uv run python -c "import sqlite3, sys; src = sqlite3.connect('data/homealize.db'); dst = sqlite3.connect(sys.argv[1]); src.backup(dst); dst.close(); src.close()" "$live\homealize.db"
$p = $live -replace '\\', '/'
$yaml = @"
storage:
  db_path: "$p/homealize.db"
  run_lock_path: "$p/run.lock"
  serve_lock_path: "$p/serve.lock"
logging:
  path: "$p/homealize.log"
export:
  dir: "$p/exports"
telegram:
  env_file: "$p/.env"
"@
[System.IO.File]::WriteAllText("$live\settings.yaml", $yaml)  # UTF-8 без BOM
uv run homealize --config "$live\settings.yaml" run --dry-run; echo "exit=$LASTEXITCODE"
uv run homealize --config "$live\settings.yaml" digest --dry-run; echo "exit=$LASTEXITCODE"
uv run homealize --config "$live\settings.yaml" telegram-test; echo "exit=$LASTEXITCODE"
```

Expected:
- `run --dry-run`:
  - настоящий обход 999.md, около минуты;
  - строки «Прогон успешен…» и «Оценка: …»;
  - «--dry-run: уведомлений к отправке N» и тексты этих уведомлений: подходящие участки, впервые увиденные за последние сутки, и снижения цен;
  - `exit=0`. При `exit=3` (сбой внешнего сервиса) записать вывод в отчёт.
- `digest --dry-run`: текст дайджеста с топ-10, `exit=0`.
- `telegram-test`: сообщение про @BotFather (в `$live` нет `.env`), `exit=2`.

Записать в отчёт задачи выдержку вывода (первые строки уведомлений и дайджеста), без токенов. Временную папку `$live` после проверки удалить:

```powershell
Remove-Item -Recurse -Force (Join-Path $env:TEMP "homealize-live")
```

- [ ] **Step 6: Commit**

```bash
git add scripts/autostart.ps1 README.md docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md
git commit -m "docs: Telegram setup, serve autostart script, stage 3 spec notes"
```
