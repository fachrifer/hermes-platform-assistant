"""Offline category recommendations built from Money Manager history."""

from __future__ import annotations

import json
from pathlib import Path

from config.settings import settings
from core.spreadsheet_finance import CategoryReference, _key


def normalize_query(merchant: str, description: str) -> str:
    """Normalize merchant/description text for recommendation matching."""
    query = _key(f"{merchant} {description}")
    replacements = (
        ("family mart", "familymart"),
        ("alfa mart", "alfamart"),
        ("indomaret", "indomaret"),
    )
    for source, target in replacements:
        query = query.replace(source, target)
    return query


class CategoryRecommendationLookup:
    """Fast local lookup using config/category_recommendations.json."""

    def __init__(self, path: str | None = None, category_reference: CategoryReference | None = None):
        self.path = Path(path or settings.finance_category_recommendations)
        self.category_reference = category_reference or CategoryReference()
        self._phrases: list[dict] = []
        self._keywords: list[dict] = []
        self._loaded = False

    def is_loaded(self) -> bool:
        self._load()
        return bool(self._phrases or self._keywords)

    def recommend(self, merchant: str, description: str) -> tuple[str, str] | None:
        """Return a canonical (category, subcategory) pair when confident."""
        self._load()
        query = normalize_query(merchant, description)
        if not query:
            return None

        phrase_hit = self._match_phrase(query)
        if phrase_hit:
            # If drink terms are present, prefer Cafe over Grocery for convenience-store coffee.
            if any(token in query for token in ("americano", "latte", "espresso", "kopi", "coffee", "cafe")):
                cafe = self._match_keywords(query)
                if cafe and _key(cafe[1]) == "cafe":
                    return cafe
            return phrase_hit

        keyword_hit = self._match_keywords(query)
        if keyword_hit:
            return keyword_hit
        return None

    def _match_phrase(self, query: str) -> tuple[str, str] | None:
        best: tuple[int, dict] | None = None
        query_tokens = {token for token in query.split() if len(token) >= 3}
        for row in self._phrases:
            match = str(row.get("match") or "")
            if not match:
                continue
            score = 0
            if match in query or query in match:
                score = len(match) * 10 + int(row.get("count") or 0)
            else:
                match_tokens = {token for token in match.split() if len(token) >= 3}
                overlap = query_tokens & match_tokens
                if len(overlap) >= 2 or (len(overlap) == 1 and next(iter(overlap)) in match and len(next(iter(overlap))) >= 5):
                    score = len(overlap) * 20 + int(row.get("count") or 0)
            if score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, row)
        if not best:
            return None
        return self._canonical(best[1])

    def _match_keywords(self, query: str) -> tuple[str, str] | None:
        tokens = {token for token in query.split() if len(token) >= 4}
        if not tokens:
            return None
        scored: list[tuple[float, dict]] = []
        for row in self._keywords:
            match = str(row.get("match") or "")
            if match not in tokens and match not in query:
                continue
            confidence = float(row.get("confidence") or 0)
            count = int(row.get("count") or 0)
            if confidence < 0.6:
                continue
            scored.append((confidence * 100 + count, row))
        if not scored:
            return None
        scored.sort(key=lambda item: -item[0])
        # Prefer drink/cafe keywords when coffee terms are present.
        if any(token in query for token in ("kopi", "cafe", "coffee", "americano", "latte")):
            for _, row in scored:
                if _key(row.get("subcategory", "")) == "cafe":
                    return self._canonical(row)
        return self._canonical(scored[0][1])

    def _canonical(self, row: dict) -> tuple[str, str] | None:
        return self.category_reference.canonical(
            str(row.get("category") or ""),
            str(row.get("subcategory") or "-") or "-",
        )

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            self._phrases = []
            self._keywords = []
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self._phrases = list(payload.get("phrases") or [])
        self._keywords = list(payload.get("keywords") or [])
