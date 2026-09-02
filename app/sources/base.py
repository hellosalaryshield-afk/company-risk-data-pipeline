from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SourceArticle:
    title: str
    description: str | None
    url: str | None
    source_name: str | None
    published_at: datetime | None
    raw_data: dict[str, Any]


@dataclass(frozen=True)
class SourceFetchResult:
    source_name: str
    records: list[SourceArticle]
    metadata: dict[str, Any]
