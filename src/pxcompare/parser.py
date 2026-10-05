"""Parser for PX (PC-Axis) files.

The parser keeps what matters for comparing two files: every metadata keyword
in every language, the variables and their values/codes, and the data cells
(numbers plus missing-value symbols such as ".." or "-").
"""

from __future__ import annotations

import codecs
import datetime as dt
import hashlib
import re
from pathlib import Path

import numpy as np

from .model import MetaEntry, MetaKey, PxFile, TimeVal, Variable


class PxParseError(ValueError):
    """The file is not a valid PX file (or uses a feature this parser does not support)."""


_STATEMENT_RE = re.compile(
    r"\s*(?P<kw>[A-Za-z0-9_-]+)\s*"
    r"(?:\[\s*(?P<lang>[^\]]*?)\s*\])?\s*"
    r'(?:\((?P<sub>(?:"[^"]*"|[^")])*)\))?\s*=',
)
_DATA_START_RE = re.compile(r"\s*DATA\s*=", re.IGNORECASE)
_VALUE_TOKEN_RE = re.compile(r'"(?P<q>[^"]*)"|(?P<comma>,)|(?P<bare>[^\s,"]+)')
_DATA_TOKEN_RE = re.compile(r'"[^"]*"|[^\s,;"]+')
_CODEPAGE_RE = re.compile(r'^\s*CODEPAGE\s*=\s*"([^"]+)"', re.MULTILINE)
_TLIST_RE = re.compile(
    r'^\s*TLIST\s*\(\s*(?P<scale>[A-Za-z]\d*)\s*'
    r'(?:,\s*"(?P<start>[^"]*)"\s*-\s*"(?P<end>[^"]*)"\s*)?\)\s*(?P<rest>.*)$',
    re.DOTALL,
)

# Keywords whose subkeys are one value per variable (stub then heading order).
CELL_KEYWORDS = {"CELLNOTE", "CELLNOTEX", "DATANOTECELL"}


def read_px(path: str | Path) -> PxFile:
    path = Path(path)
    return parse_px(path.read_bytes(), name=path.name, path=path)


def parse_px(raw: bytes, name: str = "<bytes>", path: Path | None = None) -> PxFile:
    text, encoding, warnings = _decode(raw)
    statements, data_text, data_offset = _split_statements(text)
    if data_text is None:
        raise PxParseError(f"{name}: no DATA keyword found")

    meta: dict[MetaKey, MetaEntry] = {}
    for stmt, line in statements:
        m = _STATEMENT_RE.match(stmt)
        if not m:
            raise PxParseError(f"{name}, line {line}: cannot parse {stmt.strip()[:80]!r}")
        keyword = m["kw"].upper()
        lang = m["lang"] or None
        subkeys = _parse_subkeys(m["sub"])
        raw_value = stmt[m.end():].strip()
        entry = MetaEntry(keyword, lang, subkeys, _parse_items(raw_value), raw_value, line)
        key = (keyword, lang, subkeys)
        if key in meta:
            warnings.append(f"line {line}: {keyword} repeated for the same key; values appended")
            meta[key].items.extend(entry.items)
            meta[key].raw += "\n" + entry.raw
        else:
            meta[key] = entry

    language_entry = meta.get(("LANGUAGE", None, ()))
    language = language_entry.items[0] if language_entry and language_entry.items else None
    if language is not None:
        meta = _fold_default_language(meta, language, warnings)

    languages_entry = meta.get(("LANGUAGES", None, ()))
    languages = list(languages_entry.items) if languages_entry else []
    if language is not None and language not in languages:
        languages.insert(0, language)
    elif language is not None:
        languages.remove(language)
        languages.insert(0, language)

    variables = _build_variables(meta, language, languages, name, warnings)
    numbers, symbols, uses_keys = _parse_data(data_text, variables, meta, name)

    return PxFile(
        path=path,
        name=name,
        sha256=hashlib.sha256(raw).hexdigest(),
        encoding=encoding,
        language=language,
        languages=languages,
        meta=meta,
        variables=variables,
        numbers=numbers,
        symbols=symbols,
        metadata_text=text[:data_offset],
        uses_keys=uses_keys,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- text


def _decode(raw: bytes) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    if raw.startswith(codecs.BOM_UTF8):
        try:
            return raw[len(codecs.BOM_UTF8):].decode("utf-8"), "utf-8 (BOM)", warnings
        except UnicodeDecodeError:
            warnings.append("file starts with a UTF-8 BOM but is not valid UTF-8")

    candidates: list[str] = []
    m = _CODEPAGE_RE.search(raw[:50_000].decode("latin-1"))
    if m:
        candidates.append(m.group(1).strip())
    candidates += ["utf-8", "cp1252", "latin-1"]

    for i, enc in enumerate(candidates):
        try:
            text = raw.decode(enc)
        except LookupError:
            warnings.append(f"unknown CODEPAGE {enc!r}")
            continue
        except UnicodeDecodeError:
            if m and i == 0:
                warnings.append(f"file is not valid {enc} as declared by CODEPAGE")
            continue
        return text, codecs.lookup(enc).name, warnings
    raise AssertionError("latin-1 decoding cannot fail")


def _find_statement_end(text: str, pos: int) -> int:
    """Index of the next ';' outside double quotes, or len(text)."""
    while True:
        semi = text.find(";", pos)
        quote = text.find('"', pos)
        if semi == -1:
            return len(text)
        if quote == -1 or semi < quote:
            return semi
        close = text.find('"', quote + 1)
        if close == -1:
            return len(text)
        pos = close + 1


def _split_statements(text: str) -> tuple[list[tuple[str, int]], str | None, int]:
    """Split the metadata part into statements; return (statements, data text, data offset)."""
    statements: list[tuple[str, int]] = []
    pos, line = 0, 1
    n = len(text)
    while pos < n:
        data = _DATA_START_RE.match(text, pos)
        if data:
            end = _find_statement_end(text, data.end())
            return statements, text[data.end():end], pos
        end = _find_statement_end(text, pos)
        stmt = text[pos:end]
        if stmt.strip():
            leading = len(stmt) - len(stmt.lstrip())
            statements.append((stmt, line + stmt.count("\n", 0, leading)))
        line += stmt.count("\n")
        pos = end + 1
    return statements, None, n


def _parse_subkeys(sub: str | None) -> tuple[str, ...]:
    if not sub:
        return ()
    quoted = re.findall(r'"([^"]*)"', sub)
    if quoted:
        return tuple(quoted)
    return tuple(s.strip() for s in sub.split(",") if s.strip())


def _parse_items(raw: str) -> list[str]:
    """Split a value into list items. Commas separate items; adjacent strings concatenate."""
    items: list[str] = []
    current: list[str] = []
    for m in _VALUE_TOKEN_RE.finditer(raw):
        if m["comma"] is not None:
            items.append("".join(current))
            current = []
        elif m["q"] is not None:
            current.append(m["q"])
        else:
            current.append(m["bare"])
    if current or items:
        items.append("".join(current))
    return items


def _fold_default_language(meta: dict[MetaKey, MetaEntry], language: str, warnings: list[str]) -> dict:
    """Treat KEYWORD[<default language>] the same as KEYWORD."""
    folded: dict[MetaKey, MetaEntry] = {}
    for (kw, lang, sub), entry in meta.items():
        if lang == language:
            lang = None
            entry.lang = None
        key = (kw, lang, sub)
        if key in folded:
            warnings.append(f"{kw}[{language}] duplicates {kw}; keeping the first")
            continue
        folded[key] = entry
    return folded


# ---------------------------------------------------------------------- variables


def _build_variables(
    meta: dict[MetaKey, MetaEntry], language: str | None, languages: list[str], name: str, warnings: list[str]
) -> list[Variable]:
    def items(kw: str, lang: str | None = None, sub: tuple[str, ...] = ()) -> list[str] | None:
        entry = meta.get((kw, lang, sub))
        return list(entry.items) if entry else None

    stub = items("STUB") or []
    heading = items("HEADING") or []
    if not stub and not heading:
        raise PxParseError(f"{name}: neither STUB nor HEADING is defined")
    duplicated = set(stub) & set(heading)
    if duplicated:
        raise PxParseError(f"{name}: variable(s) {sorted(duplicated)} in both STUB and HEADING")

    contvariable = (items("CONTVARIABLE") or [None])[0]
    others = [lang for lang in languages if lang != language]
    translated_layout = {
        lang: {"stub": items("STUB", lang) or [], "heading": items("HEADING", lang) or []} for lang in others
    }

    variables: list[Variable] = []
    for placement, names in (("stub", stub), ("heading", heading)):
        for pos, var in enumerate(names):
            values = items("VALUES", None, (var,))
            if values is None:
                raise PxParseError(f'{name}: VALUES("{var}") is missing')
            codes = items("CODES", None, (var,))
            if codes is not None and len(codes) != len(values):
                warnings.append(f'CODES("{var}") has {len(codes)} items but VALUES has {len(values)}; codes ignored')
                codes = None
            var_type = (items("VARIABLE-TYPE", None, (var,)) or [None])[0]
            timeval_entry = meta.get(("TIMEVAL", None, (var,)))
            timeval = _parse_timeval(timeval_entry.raw, warnings, var) if timeval_entry else None

            v = Variable(
                name=var,
                placement=placement,
                values=values,
                codes=codes,
                var_type=var_type,
                timeval=timeval,
                is_contents=(var == contvariable) or (var_type or "").lower() == "contents",
            )
            for lang in others:
                layout = translated_layout[lang][placement]
                if len(layout) != len(names):
                    continue
                v.names_by_lang[lang] = layout[pos]
                translated = items("VALUES", lang, (layout[pos],))
                if translated is not None:
                    if len(translated) != len(values):
                        warnings.append(
                            f'VALUES[{lang}]("{layout[pos]}") has {len(translated)} items but VALUES("{var}") has {len(values)}'
                        )
                    v.values_by_lang[lang] = translated
            variables.append(v)
    return variables


def _parse_timeval(raw: str, warnings: list[str], var: str) -> TimeVal | None:
    m = _TLIST_RE.match(raw)
    if not m:
        warnings.append(f'TIMEVAL("{var}") is not in TLIST format: {raw[:60]!r}')
        return None
    scale = m["scale"].upper()
    if m["start"] is not None:
        try:
            periods = _expand_periods(scale, m["start"], m["end"])
        except ValueError as exc:
            warnings.append(f'TIMEVAL("{var}"): {exc}')
            periods = [m["start"], m["end"]]
    else:
        periods = [p for p in _parse_items(m["rest"].lstrip(",")) if p != ""]
    return TimeVal(scale=scale, periods=periods)


def _expand_periods(scale: str, start: str, end: str) -> list[str]:
    """Expand a TLIST range like ("Q1", "20111", "20124") into every period in between."""
    unit = scale[0]
    sub_digits, per_year = {"A": (0, 1), "H": (1, 2), "Q": (1, 4), "M": (2, 12), "W": (2, None)}.get(unit, (None, None))
    if sub_digits is None:
        raise ValueError(f"unknown time scale {scale}")

    def split(p: str) -> tuple[int, int]:
        if len(p) != 4 + sub_digits or not p.isdigit():
            raise ValueError(f"period {p!r} does not match time scale {scale}")
        return int(p[:4]), int(p[4:] or 1)

    def periods_in(year: int) -> int:
        if per_year is not None:
            return per_year
        return dt.date(year, 12, 28).isocalendar()[1]

    year, sub = split(start)
    end_year, end_sub = split(end)
    out: list[str] = []
    while (year, sub) <= (end_year, end_sub):
        out.append(f"{year:04d}" + (f"{sub:0{sub_digits}d}" if sub_digits else ""))
        sub += 1
        if sub > periods_in(year):
            year, sub = year + 1, 1
        if len(out) > 100_000:
            raise ValueError("time range is implausibly long")
    return out


# --------------------------------------------------------------------------- data


def _to_cells(tokens: list[str]) -> tuple[np.ndarray, np.ndarray]:
    symbols = np.full(len(tokens), "", dtype=object)
    try:
        return np.array(tokens, dtype=np.float64), symbols
    except ValueError:
        pass
    numbers = np.empty(len(tokens), dtype=np.float64)
    for i, tok in enumerate(tokens):
        s = tok[1:-1] if tok.startswith('"') else tok
        try:
            numbers[i] = float(s)
        except ValueError:
            numbers[i] = np.nan
            symbols[i] = s.strip()
    return numbers, symbols


def _parse_data(
    data_text: str, variables: list[Variable], meta: dict[MetaKey, MetaEntry], name: str
) -> tuple[np.ndarray, np.ndarray, bool]:
    shape = [len(v.values) for v in variables]
    size = int(np.prod(shape, dtype=np.int64))
    tokens = _DATA_TOKEN_RE.findall(data_text)

    keyed = [
        (v, meta[("KEYS", None, (v.name,))].text.upper())
        for v in variables
        if ("KEYS", None, (v.name,)) in meta
    ]
    if not keyed:
        if len(tokens) != size:
            dims = " x ".join(f"{len(v.values)} {v.name}" for v in variables)
            raise PxParseError(f"{name}: DATA has {len(tokens)} cells but the variables define {size} ({dims})")
        numbers, symbols = _to_cells(tokens)
        return numbers, symbols, False

    # Sparse format: each row starts with the keys of the keyed stub variables;
    # rows that are left out contain only zeros.
    k = len(keyed)
    if [v for v, _ in keyed] != variables[:k] or any(v.placement != "stub" for v, _ in keyed):
        raise PxParseError(f"{name}: KEYS must be given for the leading STUB variables")
    lookups = []
    for v, kind in keyed:
        labels = v.codes if kind == "CODES" and v.codes else v.values
        lookups.append({label: i for i, label in enumerate(labels)})
    row_width = int(np.prod(shape[k:], dtype=np.int64))
    numbers = np.zeros(size, dtype=np.float64)
    symbols = np.full(size, "", dtype=object)
    step = k + row_width
    if len(tokens) % step:
        raise PxParseError(f"{name}: KEYS data has {len(tokens)} tokens, not a multiple of the row width {step}")
    for start in range(0, len(tokens), step):
        idx = []
        for lookup, tok, (v, _) in zip(lookups, tokens[start:start + k], keyed):
            key = tok.strip('"')
            if key not in lookup:
                raise PxParseError(f'{name}: unknown key {key!r} for variable "{v.name}" in DATA')
            idx.append(lookup[key])
        offset = int(np.ravel_multi_index(idx, shape[:k])) * row_width
        row_numbers, row_symbols = _to_cells(tokens[start + k:start + step])
        numbers[offset:offset + row_width] = row_numbers
        symbols[offset:offset + row_width] = row_symbols
    return numbers, symbols, True
