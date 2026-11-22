"""T-L03 — every feature of the SCANNER catalogue has an English label in the Learn rules
(frontend/src/features/learn/rules.ts), so a published strategy never shows a raw feature name."""

import re
from pathlib import Path

from app.domain.trading.agents.features import FEATURE_CATALOGUE

RULES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / "learn" / "rules.ts"


def test_every_catalogue_feature_has_an_english_label():
    text = RULES.read_text(encoding="utf-8")
    block = text[text.index("export const FEATURES"):text.index("const OP")]
    labelled = set(re.findall(r"^\s+([a-z0-9_]+): \{ subject:", block, flags=re.M))
    assert set(FEATURE_CATALOGUE) <= labelled, sorted(set(FEATURE_CATALOGUE) - labelled)
