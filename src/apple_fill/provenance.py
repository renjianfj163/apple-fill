"""Quote checks.

A sourced value has to be the whole field after the colon, or one whole
number/unit token. A shorter number inside a longer one does not count:
20 is not evidence for a quote that says 220V.
"""

from __future__ import annotations

import re
import unicodedata

_TOKEN = re.compile(r"\d+(?:\.\d+)?[a-z%]*|[a-z0-9]+(?:-[a-z0-9]+)+")


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).strip().casefold()
    text = text.replace("伏", "v").replace("瓦", "w")
    text = re.sub(r"\s+", "", text)
    return text


def segments_of(quote: str) -> set[str]:
    raw = unicodedata.normalize("NFKC", quote).strip()
    parts = {normalize_text(raw)}
    if ":" in raw:
        parts.add(normalize_text(raw.split(":", 1)[1]))
    return {part for part in parts if part}


def value_supported_by_quote(value: str, quote: str) -> bool:
    normalized = normalize_text(value)
    if not normalized:
        return False
    segments = segments_of(quote)
    if normalized in segments:
        return True
    tokens: set[str] = set()
    for segment in segments:
        tokens.update(_TOKEN.findall(segment))
    return normalized in tokens


def quote_in_page(quote: str, page_text: str) -> bool:
    normalized_quote = normalize_text(quote)
    if len(normalized_quote) < 2:
        return False
    return normalized_quote in normalize_text(page_text)
