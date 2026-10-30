from pathlib import Path

import pytest
from pydantic import ValidationError

from homealize.settlements import load_settlements

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_project_reference_loads():
    refs = load_settlements(PROJECT_ROOT / "config" / "settlements.yaml")
    assert len(refs) == 18
    assert (refs[13932].name, refs[13932].score) == ("Яловены", 100)
    assert refs[13917].score == 40
    assert refs[14398].potential == "Высокий"


def test_duplicate_id_is_rejected(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(
        "- {id: 1, name: A, potential: X, score: 50}\n- {id: 1, name: B, potential: Y, score: 60}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="дважды"):
        load_settlements(path)


def test_score_out_of_range_is_rejected(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("- {id: 1, name: A, potential: X, score: 150}\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settlements(path)
