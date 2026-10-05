"""In-memory representation of a parsed PX file."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Metadata is keyed by (KEYWORD, language, subkeys). language is None for the
# file's default language (the LANGUAGE keyword).
MetaKey = tuple[str, "str | None", tuple[str, ...]]


@dataclass
class MetaEntry:
    keyword: str
    lang: str | None
    subkeys: tuple[str, ...]
    items: list[str]
    """Parsed value: one item per comma-separated element, adjacent quoted strings joined."""
    raw: str
    """Value text exactly as written after the '=' sign."""
    line: int

    @property
    def text(self) -> str:
        """Value as a single display string."""
        if len(self.items) == 1:
            return self.items[0]
        return ", ".join(f'"{i}"' for i in self.items)


@dataclass
class TimeVal:
    scale: str
    """TLIST time scale, e.g. A1, H1, Q1, M1, W1."""
    periods: list[str]
    """Periods in PX notation, e.g. "2024" or "20241" (2024 Q1)."""


@dataclass
class Variable:
    name: str
    """Name in the default language."""
    placement: str
    """"stub" or "heading"."""
    values: list[str]
    """Value texts in the default language, in file order."""
    codes: list[str] | None
    var_type: str | None = None
    timeval: TimeVal | None = None
    is_contents: bool = False
    names_by_lang: dict[str, str] = field(default_factory=dict)
    values_by_lang: dict[str, list[str]] = field(default_factory=dict)

    @property
    def is_time(self) -> bool:
        return self.timeval is not None or (self.var_type or "").lower() == "time"

    def name_in(self, lang: str | None) -> str:
        if lang is None:
            return self.name
        return self.names_by_lang.get(lang, self.name)

    def values_in(self, lang: str | None) -> list[str]:
        if lang is None:
            return self.values
        translated = self.values_by_lang.get(lang)
        return translated if translated and len(translated) == len(self.values) else self.values


@dataclass
class PxFile:
    path: Path | None
    name: str
    sha256: str
    encoding: str
    language: str | None
    """Default language (LANGUAGE keyword)."""
    languages: list[str]
    """All languages in the file, default first."""
    meta: dict[MetaKey, MetaEntry]
    variables: list[Variable]
    """Stub variables followed by heading variables; this is the data cube's axis order."""
    numbers: np.ndarray
    """Cell values as float64 in PX order; NaN where the cell holds a symbol."""
    symbols: np.ndarray
    """Missing-value symbol per cell (e.g. "..", "-"); "" for numeric cells."""
    metadata_text: str
    """Decoded file text up to the DATA keyword, for raw text diffs."""
    uses_keys: bool = False
    warnings: list[str] = field(default_factory=list)

    def get(self, keyword: str, lang: str | None = None, subkeys: tuple[str, ...] = ()) -> MetaEntry | None:
        return self.meta.get((keyword, lang, subkeys))

    def get_text(self, keyword: str, lang: str | None = None, subkeys: tuple[str, ...] = ()) -> str | None:
        entry = self.get(keyword, lang, subkeys)
        return entry.text if entry else None

    @property
    def other_languages(self) -> list[str]:
        return [lang for lang in self.languages if lang != self.language]

    @property
    def stub(self) -> list[Variable]:
        return [v for v in self.variables if v.placement == "stub"]

    @property
    def heading(self) -> list[Variable]:
        return [v for v in self.variables if v.placement == "heading"]

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(len(v.values) for v in self.variables)

    @property
    def size(self) -> int:
        return int(self.numbers.size)

    def variable(self, name: str) -> Variable | None:
        for v in self.variables:
            if v.name == name:
                return v
        return None
