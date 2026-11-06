"""Scoring configuration: every weight, point value and threshold, versioned.

Change a value only together with `weights_version`, and every stored score records the
version that produced it (Developer Instructions v1, sections 7 and 10).
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).with_name("weights_v1.json")

PILLARS = ("financial", "sentiment", "news", "profile")


@dataclass(frozen=True)
class FeatureSpec:
    key: str
    label: str
    pillar: str
    weight: float
    higher_is_safer: bool
    binary: bool = False


@lru_cache
def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def feature_specs(config: dict[str, Any], listed: bool) -> list[FeatureSpec]:
    """Features for one company. Listed companies use the listed financial set, others
    the unlisted set (section 4)."""
    groups = {
        "financial": config["features"]["financial_listed" if listed else "financial_unlisted"],
        "sentiment": config["features"]["sentiment"],
        "profile": config["features"]["profile"],
    }
    return [
        FeatureSpec(
            key=item["key"],
            label=item["label"],
            pillar=pillar,
            weight=float(item["weight"]),
            higher_is_safer=item["direction"] == "safer",
            binary=bool(item.get("binary", False)),
        )
        for pillar, items in groups.items()
        for item in items
    ]
