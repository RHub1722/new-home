"""Справочник сёл из code_artifact.md: потенциал населённого пункта для оценки (спека §5, §6.5)."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class SettlementRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int  # номер населённого пункта на 999.md (feature 8)
    name: str
    potential: str  # формулировка из документа
    score: float = Field(ge=0, le=100)


def load_settlements(path: Path) -> dict[int, SettlementRef]:
    """Читает справочник; id сёл должны быть уникальны."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    by_id: dict[int, SettlementRef] = {}
    for item in data:
        ref = SettlementRef.model_validate(item)
        if ref.id in by_id:
            raise ValueError(f"settlements: id {ref.id} встречается дважды")
        by_id[ref.id] = ref
    return by_id
