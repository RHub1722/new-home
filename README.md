# Homealize

Мониторинг участков под строительство дома на 999.md. Этап 1: сбор объявлений, история цен, отметки «отфильтровано» и «снято с продажи».

Спецификация: `docs/superpowers/specs/2026-10-29-homealize-land-monitor-design.md`.

## Быстрый старт

```bash
uv sync
uv run homealize init-db
uv run homealize run
```

- Настройки: `config/settings.yaml`.
- База: `data/homealize.db` (SQLite).
- Логи: `logs/homealize.log`.

Один прогон делает около 23 запросов к 999.md с паузами 2–3 с и занимает около минуты.

## Тесты

```bash
uv run pytest
uv run pytest -m live
```

Первая команда запускает офлайн-тесты. Вторая делает один настоящий запрос к 999.md и проверяет, что формат ответа не изменился.
