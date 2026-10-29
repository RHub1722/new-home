"""Общий интерфейс источника объявлений и его ошибки."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from homealize.models import Listing


class SourceError(Exception):
    """Базовая ошибка источника объявлений."""


class SourceUnavailableError(SourceError):
    """Сайт не ответил нормально после всех повторов."""


class RateLimitedError(SourceError):
    """Сайт показал страницу превышения лимита запросов."""


class SourceSchemaError(SourceError):
    """Ответ сайта не похож на ожидаемый: вероятно, сайт что-то поменял."""


class EmptySearchError(SourceError):
    """Поиск вернул 0 объявлений: вероятно, сайт что-то поменял."""


@dataclass(frozen=True)
class Page:
    """Одна страница результатов поиска."""

    listings: list[Listing]
    total_count: int
    parse_errors: list[str] = field(default_factory=list)
    unparsed_ids: list[str] = field(default_factory=list)


class Source(Protocol):
    name: str

    def iter_pages(self) -> Iterator[Page]: ...

    def close(self) -> None: ...
