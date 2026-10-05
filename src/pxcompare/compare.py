"""Compare two parsed PX files.

The comparison is semantic: both files are parsed, variables are matched by
name and values by code (falling back to text), and only then are data cells
compared. A reordered dimension or a variable moved from STUB to HEADING is
therefore reported once, instead of as "every cell differs".

Five checks are reported, each SAME / DIFF / SKIPPED:

  metadata   every keyword in every language, except structural keywords
             (handled by the checks below) and ignored volatile keywords
  variables  which variables exist, STUB/HEADING layout, translated names
  values     values and codes of non-time variables, incl. translations
  time       periods of the time variable(s) and TIMEVAL
  data       cell-by-cell comparison on the cells both files share
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import numpy as np
import pandas as pd

from .model import PxFile, Variable
from .parser import CELL_KEYWORDS, read_px

DEFAULT_IGNORED_KEYWORDS = frozenset({"CREATION-DATE", "LAST-UPDATED", "NEXT-UPDATE"})

# Compared by the variables/values/time/data checks instead of the metadata check.
STRUCTURAL_KEYWORDS = frozenset({"STUB", "HEADING", "VALUES", "CODES", "TIMEVAL", "KEYS", "DATA"})


class Status(str, Enum):
    SAME = "same"
    DIFF = "diff"
    SKIPPED = "skipped"


class Verdict(str, Enum):
    IDENTICAL = "identical"
    """Byte-for-byte identical files."""
    EQUIVALENT = "equivalent"
    """Same content; only formatting (line wrapping, number notation, keyword order) or ignored keywords differ."""
    DIFFERENT = "different"

    @property
    def ok(self) -> bool:
        return self is not Verdict.DIFFERENT


@dataclass
class CompareOptions:
    ignore_keywords: frozenset[str] = DEFAULT_IGNORED_KEYWORDS
    abs_tol: float = 0.0
    rel_tol: float = 0.0


@dataclass
class Check:
    key: str
    title: str
    status: Status
    summary: str


# ----------------------------------------------------------------------- metadata


@dataclass
class MetaChange:
    keyword: str
    lang: str | None
    subkeys: tuple[str, ...]
    baseline: str | None
    """None when the keyword only exists in the candidate."""
    candidate: str | None
    """None when the keyword only exists in the baseline."""

    @property
    def label(self) -> str:
        lang = f"[{self.lang}]" if self.lang else ""
        sub = "(" + ",".join(f'"{s}"' for s in self.subkeys) + ")" if self.subkeys else ""
        return f"{self.keyword}{lang}{sub}"

    @property
    def kind(self) -> str:
        if self.baseline is None:
            return "added"
        if self.candidate is None:
            return "removed"
        return "changed"


@dataclass
class MetadataDiff:
    changes: list[MetaChange]
    ignored: list[MetaChange]
    """Ignored keywords, listed whether or not they differ."""
    compared: list[MetaChange]
    """Every compared keyword, equal or not, for side-by-side browsing."""

    @property
    def n_compared(self) -> int:
        return len(self.compared)


# ---------------------------------------------------------------------- variables


@dataclass
class VariablesDiff:
    pairs: list[tuple[Variable, Variable]]
    only_in_baseline: list[str]
    only_in_candidate: list[str]
    layout_baseline: tuple[list[str], list[str]]
    layout_candidate: tuple[list[str], list[str]]
    """(stub, heading) of the candidate, using baseline names where variables were matched."""
    layout_changed: bool
    renamed: list[tuple[str, str]]
    """(baseline name, candidate name) for variables matched by code or translation rather than name."""
    translation_changes: list[tuple[str, str, str, str]]
    """(variable, language, baseline name, candidate name)."""


@dataclass
class ValuesDiff:
    """Value-level differences for one matched variable."""

    variable: str
    is_time: bool
    matched_by: str
    pairs: list[tuple[int, int]]
    """(baseline index, candidate index) of matched values, in baseline order."""
    removed: list[str]
    added: list[str]
    relabelled: list[tuple[str, str, str]]
    """(code, baseline text, candidate text)."""
    code_changes: list[tuple[str, str, str]]
    """(text, baseline code, candidate code)."""
    translation_changes: list[tuple[str, str, str, str]]
    """(language, value, baseline text, candidate text)."""
    order_changed: bool
    timeval_note: str | None = None
    baseline_range: str = ""
    candidate_range: str = ""
    n_baseline: int = 0
    n_candidate: int = 0

    @property
    def differs(self) -> bool:
        return bool(
            self.removed or self.added or self.relabelled or self.code_changes
            or self.translation_changes or self.order_changed or self.timeval_note
        )

    def describe(self) -> str:
        parts = []
        if self.added:
            parts.append(f"{len(self.added)} added ({_preview(self.added)})")
        if self.removed:
            parts.append(f"{len(self.removed)} removed ({_preview(self.removed)})")
        if self.relabelled:
            parts.append(f"{len(self.relabelled)} relabelled")
        if self.code_changes:
            parts.append(_n(len(self.code_changes), "code change"))
        if self.translation_changes:
            parts.append(_n(len(self.translation_changes), "translation change"))
        if self.order_changed:
            parts.append("order changed")
        if self.timeval_note:
            parts.append(self.timeval_note)
        return "; ".join(parts) if parts else "same"


# --------------------------------------------------------------------------- data


@dataclass
class DataDiff:
    comparable: bool
    reason: str = ""
    n_baseline: int = 0
    n_candidate: int = 0
    n_common: int = 0
    n_diff: int = 0
    n_symbol_diff: int = 0
    """Differing cells where at least one side is a symbol such as ".." rather than a number."""
    max_abs: float = 0.0
    max_rel: float = 0.0
    variables: list[Variable] = field(default_factory=list)
    """Baseline variables, in baseline axis order; idx columns refer to these."""
    common_counts: list[int] = field(default_factory=list)
    """Number of matched values per variable (to say "12 of 27 cells")."""
    idx: np.ndarray = field(default_factory=lambda: np.empty((0, 0), dtype=np.int64))
    """(n_diff, n_variables) baseline value indices of each differing cell."""
    baseline_numbers: np.ndarray = field(default_factory=lambda: np.empty(0))
    candidate_numbers: np.ndarray = field(default_factory=lambda: np.empty(0))
    baseline_symbols: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=object))
    candidate_symbols: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=object))

    def table(self, lang: str | None = None, limit: int | None = None) -> pd.DataFrame:
        """One row per differing cell, labelled in the given language."""
        n = self.n_diff if limit is None else min(limit, self.n_diff)
        columns: dict[str, object] = {}
        for k, var in enumerate(self.variables):
            labels = np.array(var.values_in(lang), dtype=object)
            columns[var.name_in(lang)] = labels[self.idx[:n, k]]
        a_num, b_num = self.baseline_numbers[:n], self.candidate_numbers[:n]
        a_sym, b_sym = self.baseline_symbols[:n], self.candidate_symbols[:n]
        columns["Baseline"] = [_cell(x, s) for x, s in zip(a_num, a_sym)]
        columns["Candidate"] = [_cell(x, s) for x, s in zip(b_num, b_sym)]
        with np.errstate(divide="ignore", invalid="ignore"):
            delta = b_num - a_num
            rel = np.where(a_num != 0, delta / np.abs(a_num) * 100, np.nan)
        columns["Difference"] = delta
        columns["Difference %"] = rel
        return pd.DataFrame(columns)

    def breakdown(self, lang: str | None = None) -> list[tuple[Variable, list[tuple[str, int, int]]]]:
        """Per variable: (value label, differing cells, cells compared) for values with differences."""
        out = []
        for k, var in enumerate(self.variables):
            labels = var.values_in(lang)
            counts = np.bincount(self.idx[:, k], minlength=len(labels)) if self.n_diff else np.zeros(len(labels), int)
            per_value = self.n_common // self.common_counts[k] if self.common_counts[k] else 0
            rows = [(labels[i], int(c), per_value) for i, c in enumerate(counts) if c]
            rows.sort(key=lambda r: -r[1])
            out.append((var, rows))
        return out

    def crosstab(self, row: int, col: int, lang: str | None = None) -> pd.DataFrame:
        """Differing-cell counts by two variables (indices into self.variables)."""
        rv, cv = self.variables[row], self.variables[col]
        grid = np.zeros((len(rv.values), len(cv.values)), dtype=np.int64)
        np.add.at(grid, (self.idx[:, row], self.idx[:, col]), 1)
        return pd.DataFrame(grid, index=rv.values_in(lang), columns=cv.values_in(lang))


# --------------------------------------------------------------------- comparison


@dataclass
class Comparison:
    baseline: PxFile
    candidate: PxFile
    options: CompareOptions
    byte_identical: bool
    metadata: MetadataDiff
    variables: VariablesDiff
    values: list[ValuesDiff]
    data: DataDiff
    checks: list[Check]
    hints: list[str]

    @property
    def verdict(self) -> Verdict:
        if self.byte_identical:
            return Verdict.IDENTICAL
        if any(c.status is Status.DIFF for c in self.checks):
            return Verdict.DIFFERENT
        return Verdict.EQUIVALENT

    @property
    def ok(self) -> bool:
        return self.verdict.ok

    @property
    def failed_checks(self) -> list[Check]:
        return [c for c in self.checks if c.status is Status.DIFF]

    def check(self, key: str) -> Check:
        return next(c for c in self.checks if c.key == key)

    @property
    def headline(self) -> str:
        verdict = self.verdict
        if verdict is Verdict.IDENTICAL:
            return "Identical: the files are byte-for-byte the same"
        if verdict is Verdict.EQUIVALENT:
            return "Equivalent: same content; only formatting or ignored keywords differ"
        return "Different: " + ", ".join(c.title for c in self.failed_checks)

    def to_dict(self, max_cells: int = 1000) -> dict:
        table = self.data.table(limit=max_cells) if self.data.comparable else pd.DataFrame()
        return {
            "baseline": str(self.baseline.path or self.baseline.name),
            "candidate": str(self.candidate.path or self.candidate.name),
            "verdict": self.verdict.value,
            "headline": self.headline,
            "checks": [{"key": c.key, "title": c.title, "status": c.status.value, "summary": c.summary} for c in self.checks],
            "hints": self.hints,
            "metadata_changes": [
                {"keyword": m.label, "kind": m.kind, "baseline": m.baseline, "candidate": m.candidate}
                for m in self.metadata.changes
            ],
            "values": [
                {"variable": v.variable, "summary": v.describe(), "added": v.added, "removed": v.removed}
                for v in self.values if v.differs
            ],
            "data": {
                "comparable": self.data.comparable,
                "reason": self.data.reason,
                "cells_baseline": self.data.n_baseline,
                "cells_candidate": self.data.n_candidate,
                "cells_compared": self.data.n_common,
                "cells_differing": self.data.n_diff,
                "max_abs_difference": _json_float(self.data.max_abs),
                "max_rel_difference": _json_float(self.data.max_rel),
                "differences": [
                    {k: (_json_float(v) if isinstance(v, float) else v) for k, v in row.items()}
                    for row in table.to_dict(orient="records")
                ],
            },
        }


def compare_files(baseline: str | Path, candidate: str | Path, options: CompareOptions | None = None) -> Comparison:
    return compare(read_px(baseline), read_px(candidate), options)


def compare(a: PxFile, b: PxFile, options: CompareOptions | None = None) -> Comparison:
    options = options or CompareOptions()
    variables = _compare_variables(a, b)
    values = [_compare_values(va, vb) for va, vb in variables.pairs]
    metadata = _compare_metadata(a, b, variables, values, options)
    data = _compare_data(a, b, variables, values, options)
    checks = _build_checks(a, variables, values, metadata, data)
    return Comparison(
        baseline=a,
        candidate=b,
        options=options,
        byte_identical=a.sha256 == b.sha256,
        metadata=metadata,
        variables=variables,
        values=values,
        data=data,
        checks=checks,
        hints=_hints(variables, values, data),
    )


# ------------------------------------------------------------------- the checks


def _compare_variables(a: PxFile, b: PxFile) -> VariablesDiff:
    unmatched_b = list(b.variables)
    pairs: list[tuple[Variable, Variable]] = []
    renamed: list[tuple[str, str]] = []

    def take(pred) -> Variable | None:
        for vb in unmatched_b:
            if pred(vb):
                unmatched_b.remove(vb)
                return vb
        return None

    unmatched_a = []
    for va in a.variables:
        vb = take(lambda v: v.name == va.name)
        if vb is None:
            code_a = a.get_text("VARIABLECODE", None, (va.name,))
            vb = take(lambda v: code_a is not None and code_a == b.get_text("VARIABLECODE", None, (v.name,)))
            if vb is None:
                vb = take(lambda v: any(v.names_by_lang.get(lang) == name for lang, name in va.names_by_lang.items()))
            if vb is not None:
                renamed.append((va.name, vb.name))
        if vb is None:
            unmatched_a.append(va.name)
        else:
            pairs.append((va, vb))

    to_a_name = {vb.name: va.name for va, vb in pairs}
    layout_a = ([v.name for v in a.stub], [v.name for v in a.heading])
    layout_b = ([to_a_name.get(v.name, v.name) for v in b.stub], [to_a_name.get(v.name, v.name) for v in b.heading])

    translation_changes = []
    for va, vb in pairs:
        for lang in sorted(set(va.names_by_lang) & set(vb.names_by_lang)):
            if va.names_by_lang[lang] != vb.names_by_lang[lang]:
                translation_changes.append((va.name, lang, va.names_by_lang[lang], vb.names_by_lang[lang]))
        for lang in sorted(set(va.names_by_lang) ^ set(vb.names_by_lang)):
            translation_changes.append((va.name, lang, va.names_by_lang.get(lang, "—"), vb.names_by_lang.get(lang, "—")))

    return VariablesDiff(
        pairs=pairs,
        only_in_baseline=unmatched_a,
        only_in_candidate=[v.name for v in unmatched_b],
        layout_baseline=layout_a,
        layout_candidate=layout_b,
        layout_changed=layout_a != layout_b,
        renamed=renamed,
        translation_changes=translation_changes,
    )


def _match_keys(va: Variable, vb: Variable) -> tuple[list[str], list[str], str]:
    if va.codes and vb.codes and _unique(va.codes) and _unique(vb.codes):
        return va.codes, vb.codes, "code"
    if _unique(va.values) and _unique(vb.values):
        return va.values, vb.values, "text"
    return [str(i) for i in range(len(va.values))], [str(i) for i in range(len(vb.values))], "position"


def _compare_values(va: Variable, vb: Variable) -> ValuesDiff:
    keys_a, keys_b, matched_by = _match_keys(va, vb)
    pos_b = {k: j for j, k in enumerate(keys_b)}
    pairs = [(i, pos_b[k]) for i, k in enumerate(keys_a) if k in pos_b]

    code_changes = []
    if matched_by == "code":
        # A value whose code changed but whose text did not is still the same value.
        left_a = {i for i in range(len(va.values))} - {i for i, _ in pairs}
        left_b = {j for j in range(len(vb.values))} - {j for _, j in pairs}
        text_b = {vb.values[j]: j for j in left_b}
        for i in sorted(left_a):
            j = text_b.get(va.values[i])
            if j is not None:
                pairs.append((i, j))
                code_changes.append((va.values[i], va.codes[i], vb.codes[j]))
        pairs.sort()

    matched_a = {i for i, _ in pairs}
    matched_b = {j for _, j in pairs}
    removed = [va.values[i] for i in range(len(va.values)) if i not in matched_a]
    added = [vb.values[j] for j in range(len(vb.values)) if j not in matched_b]
    relabelled = [
        (keys_a[i], va.values[i], vb.values[j]) for i, j in pairs if va.values[i] != vb.values[j]
    ]
    translation_changes = []
    for lang in sorted(set(va.values_by_lang) & set(vb.values_by_lang)):
        ta, tb = va.values_by_lang[lang], vb.values_by_lang[lang]
        for i, j in pairs:
            if i < len(ta) and j < len(tb) and ta[i] != tb[j]:
                translation_changes.append((lang, va.values[i], ta[i], tb[j]))
    b_order = [j for _, j in pairs]
    order_changed = b_order != sorted(b_order)

    diff = ValuesDiff(
        variable=va.name,
        is_time=va.is_time or vb.is_time,
        matched_by=matched_by,
        pairs=pairs,
        removed=removed,
        added=added,
        relabelled=relabelled,
        code_changes=code_changes,
        translation_changes=translation_changes,
        order_changed=order_changed,
        n_baseline=len(va.values),
        n_candidate=len(vb.values),
        baseline_range=_range(va.values),
        candidate_range=_range(vb.values),
    )
    if diff.is_time:
        diff.timeval_note = _timeval_note(va, vb, pairs)
    return diff


def _timeval_note(va: Variable, vb: Variable, pairs: list[tuple[int, int]]) -> str | None:
    ta, tb = va.timeval, vb.timeval
    if ta is None and tb is None:
        return None
    if ta is None or tb is None:
        return "TIMEVAL only in " + ("candidate" if ta is None else "baseline")
    if ta.scale != tb.scale:
        return f"time scale {ta.scale} → {tb.scale}"
    if len(ta.periods) == len(va.values) and len(tb.periods) == len(vb.values):
        changed = [(va.values[i], ta.periods[i], tb.periods[j]) for i, j in pairs if ta.periods[i] != tb.periods[j]]
        if changed:
            value, pa, pb = changed[0]
            return f"TIMEVAL differs for {len(changed)} periods (e.g. {value}: {pa} → {pb})"
    return None


def _compare_metadata(
    a: PxFile, b: PxFile, variables: VariablesDiff, values: list[ValuesDiff], options: CompareOptions
) -> MetadataDiff:
    # Express every key in the baseline's default-language names so that e.g.
    # UNITS[sv]("Godsmängd") lines up between files even if a label changed.
    b_to_a_var = {vb.name: va.name for va, vb in variables.pairs}
    value_maps = {}
    for (va, vb), vd in zip(variables.pairs, values):
        value_maps[vb.name] = {vb.values[j]: va.values[i] for i, j in vd.pairs}

    def canon_b(kw: str, sub: tuple[str, ...]) -> tuple[str, ...]:
        if kw in CELL_KEYWORDS and len(sub) == len(b.variables):
            return tuple(
                s if s == "*" else value_maps.get(v.name, {}).get(s, s) for s, v in zip(sub, b.variables)
            )
        out, prev = [], None
        for s in sub:
            if prev is not None:
                out.append(value_maps.get(prev, {}).get(s, s))
                prev = None
            elif s in b_to_a_var:
                out.append(b_to_a_var[s])
                prev = s
            else:
                cont = next((v for v in b.variables if v.is_contents), None)
                out.append(value_maps.get(cont.name, {}).get(s, s) if cont else s)
        return tuple(out)

    ka = {_canonical(a, kw, lang, sub): e for (kw, lang, sub), e in a.meta.items()}
    kb = {}
    for (kw, lang, sub), e in b.meta.items():
        default_sub = _canonical(b, kw, lang, sub)[2]
        kb[(kw, lang, canon_b(kw, default_sub))] = e

    changes, ignored, compared = [], [], []
    for key in list(ka) + [k for k in kb if k not in ka]:
        kw, lang, sub = key
        if kw in STRUCTURAL_KEYWORDS:
            continue
        ea, eb = ka.get(key), kb.get(key)
        shown = (ea or eb).subkeys
        change = MetaChange(kw, lang, shown, ea.text if ea else None, eb.text if eb else None)
        if kw in options.ignore_keywords:
            ignored.append(change)
            continue
        compared.append(change)
        if ea is None or eb is None or ea.items != eb.items:
            changes.append(change)
    return MetadataDiff(changes=changes, ignored=ignored, compared=compared)


def _canonical(f: PxFile, kw: str, lang: str | None, sub: tuple[str, ...]) -> tuple[str, str | None, tuple[str, ...]]:
    """Translate subkeys written in `lang` into the file's default-language names."""
    if lang is None or not sub:
        return kw, lang, sub
    var_names = {v.names_by_lang.get(lang): v for v in f.variables if lang in v.names_by_lang}

    def value_map(v: Variable) -> dict[str, str]:
        return dict(zip(v.values_by_lang.get(lang, []), v.values))

    if kw in CELL_KEYWORDS and len(sub) == len(f.variables):
        return kw, lang, tuple(s if s == "*" else value_map(v).get(s, s) for s, v in zip(sub, f.variables))
    out, prev = [], None
    for s in sub:
        if prev is not None:
            out.append(value_map(prev).get(s, s))
            prev = None
        elif s in var_names:
            prev = var_names[s]
            out.append(prev.name)
        else:
            cont = next((v for v in f.variables if v.is_contents), None)
            out.append(value_map(cont).get(s, s) if cont else s)
    return kw, lang, tuple(out)


def _compare_data(
    a: PxFile, b: PxFile, variables: VariablesDiff, values: list[ValuesDiff], options: CompareOptions
) -> DataDiff:
    base = DataDiff(comparable=False, n_baseline=a.size, n_candidate=b.size, variables=list(a.variables))
    if variables.only_in_baseline or variables.only_in_candidate:
        base.reason = "the files do not have the same variables"
        return base
    if any(not v.pairs for v in values):
        empty = next(v.variable for v in values if not v.pairs)
        base.reason = f'no values of "{empty}" match between the files'
        return base

    cube_a_num = a.numbers.reshape(a.shape)
    cube_a_sym = a.symbols.reshape(a.shape)
    b_axis = {v.name: k for k, v in enumerate(b.variables)}
    perm = [b_axis[vb.name] for _, vb in variables.pairs]
    cube_b_num = b.numbers.reshape(b.shape).transpose(perm)
    cube_b_sym = b.symbols.reshape(b.shape).transpose(perm)

    ia = [np.array([i for i, _ in v.pairs]) for v in values]
    ib = [np.array([j for _, j in v.pairs]) for v in values]
    a_num, a_sym = cube_a_num[np.ix_(*ia)], cube_a_sym[np.ix_(*ia)]
    b_num, b_sym = cube_b_num[np.ix_(*ib)], cube_b_sym[np.ix_(*ib)]

    both_numeric = (a_sym == "") & (b_sym == "")
    if options.abs_tol or options.rel_tol:
        tol = np.maximum(options.abs_tol, options.rel_tol * np.maximum(np.abs(a_num), np.abs(b_num)))
        numbers_equal = np.abs(a_num - b_num) <= tol
    else:
        numbers_equal = a_num == b_num
    equal = np.where(both_numeric, numbers_equal, a_sym == b_sym)
    diff_mask = ~equal

    where = np.nonzero(diff_mask)
    idx = np.stack([ia[k][where[k]] for k in range(len(ia))], axis=1)

    numeric_diffs = both_numeric & diff_mask
    abs_d = np.abs(a_num - b_num)[numeric_diffs]
    with np.errstate(divide="ignore", invalid="ignore"):
        rel_d = (abs_d / np.abs(a_num[numeric_diffs]))
    rel_d = rel_d[np.isfinite(rel_d)]

    return DataDiff(
        comparable=True,
        n_baseline=a.size,
        n_candidate=b.size,
        n_common=int(a_num.size),
        n_diff=int(diff_mask.sum()),
        n_symbol_diff=int((diff_mask & ~both_numeric).sum()),
        max_abs=float(abs_d.max()) if abs_d.size else 0.0,
        max_rel=float(rel_d.max()) if rel_d.size else 0.0,
        variables=list(a.variables),
        common_counts=[len(v.pairs) for v in values],
        idx=idx.astype(np.int64),
        baseline_numbers=a_num[diff_mask],
        candidate_numbers=b_num[diff_mask],
        baseline_symbols=a_sym[diff_mask],
        candidate_symbols=b_sym[diff_mask],
    )


def _build_checks(
    a: PxFile, variables: VariablesDiff, values: list[ValuesDiff], metadata: MetadataDiff, data: DataDiff
) -> list[Check]:
    checks = []

    # metadata
    if metadata.changes:
        kinds = {k: sum(1 for c in metadata.changes if c.kind == k) for k in ("changed", "added", "removed")}
        detail = ", ".join(f"{n} {k}" for k, n in kinds.items() if n)
        summary = f"{_n(len(metadata.changes), 'difference')} ({detail}): {_preview([c.label for c in metadata.changes])}"
    else:
        summary = f"all {metadata.n_compared} keywords match"
    if metadata.ignored:
        summary += f" (ignored: {', '.join(sorted({c.keyword for c in metadata.ignored}))})"
    checks.append(Check("metadata", "Table metadata", Status.DIFF if metadata.changes else Status.SAME, summary))

    # variables
    problems = []
    if variables.only_in_baseline:
        problems.append("only in baseline: " + ", ".join(variables.only_in_baseline))
    if variables.only_in_candidate:
        problems.append("only in candidate: " + ", ".join(variables.only_in_candidate))
    if variables.renamed:
        problems.append("renamed: " + ", ".join(f"{x} → {y}" for x, y in variables.renamed))
    if variables.layout_changed:
        problems.append(f"layout {_layout(variables.layout_baseline)} → {_layout(variables.layout_candidate)}")
    if variables.translation_changes:
        problems.append(_n(len(variables.translation_changes), "translated name") + " differ")
    if problems:
        checks.append(Check("variables", "Variables", Status.DIFF, "; ".join(problems)))
    else:
        checks.append(Check(
            "variables", "Variables", Status.SAME,
            f"{len(variables.pairs)} variables, same layout {_layout(variables.layout_baseline)}",
        ))

    # values (non-time)
    plain = [v for v in values if not v.is_time]
    differing = [v for v in plain if v.differs]
    if differing:
        summary = "; ".join(f"{v.variable}: {v.describe()}" for v in differing)
        checks.append(Check("values", "Values & codes", Status.DIFF, summary))
    elif plain:
        n_values = sum(v.n_baseline for v in plain)
        checks.append(Check("values", "Values & codes", Status.SAME, f"all {n_values} values match in {len(plain)} variables"))
    else:
        checks.append(Check("values", "Values & codes", Status.SKIPPED, "no matched non-time variables"))

    # time
    time = [v for v in values if v.is_time]
    if not time:
        has_time = any(v.is_time for v in a.variables)
        checks.append(Check("time", "Time periods", Status.DIFF if has_time else Status.SKIPPED,
                            "time variable missing from candidate" if has_time else "no time variable"))
    else:
        parts, status = [], Status.SAME
        for v in time:
            if v.differs:
                status = Status.DIFF
                rng = f"{v.baseline_range} ({v.n_baseline}) → {v.candidate_range} ({v.n_candidate})"
                parts.append(f"{v.variable}: {rng}; {v.describe()}")
            else:
                parts.append(f"{v.variable}: {v.baseline_range} ({v.n_baseline} periods) in both")
        checks.append(Check("time", "Time periods", status, "; ".join(parts)))

    # data
    if not data.comparable:
        checks.append(Check("data", "Data", Status.SKIPPED, f"not compared: {data.reason}"))
    else:
        extra = []
        if data.n_baseline != data.n_common:
            extra.append(f"{data.n_baseline - data.n_common:,} cells only in baseline")
        if data.n_candidate != data.n_common:
            extra.append(f"{data.n_candidate - data.n_common:,} cells only in candidate")
        extra_text = f" ({'; '.join(extra)})" if extra else ""
        if data.n_diff:
            share = data.n_diff / data.n_common * 100
            parts = [f"{data.n_diff:,} of {data.n_common:,} compared cells differ ({share:.1f} %)"]
            if data.n_diff > data.n_symbol_diff:
                parts.append(f"max |Δ| {format_number(data.max_abs)}")
            if data.n_symbol_diff:
                parts.append(f"{data.n_symbol_diff:,} with symbols such as \"..\"")
            checks.append(Check("data", "Data", Status.DIFF, ", ".join(parts) + extra_text))
        else:
            checks.append(Check("data", "Data", Status.SAME, f"all {data.n_common:,} compared cells match{extra_text}"))
    return checks


def _hints(variables: VariablesDiff, values: list[ValuesDiff], data: DataDiff) -> list[str]:
    hints = []
    if data.comparable and data.n_common and data.n_diff / data.n_common >= 0.5:
        hints.append(
            "Most cells differ. That usually means the runs did not use the same input data, "
            "or a dimension is misaligned (e.g. values that swapped codes)."
        )
    if any(v.is_time and (v.added or v.removed) for v in values):
        hints.append("The time periods differ. Check that both runs used the same input data period.")
    if any(v.order_changed for v in values) or variables.layout_changed:
        hints.append(
            "Variables or values are in a different order. The data was aligned by code before "
            "comparing, so the Data check is still cell-for-cell."
        )
    return hints


# -------------------------------------------------------------------- formatting


def _n(count: int, noun: str) -> str:
    return f"{count:,} {noun}" + ("" if count == 1 else "s")


def _unique(items: list[str]) -> bool:
    return len(set(items)) == len(items)


def _range(values: list[str]) -> str:
    if not values:
        return "(none)"
    return values[0] if len(values) == 1 else f"{values[0]}–{values[-1]}"


def _layout(layout: tuple[list[str], list[str]]) -> str:
    stub, heading = layout
    return f"[stub: {', '.join(stub) or '—'} | heading: {', '.join(heading) or '—'}]"


def _preview(items: list[str], n: int = 3) -> str:
    shown = ", ".join(items[:n])
    return shown + (f", … (+{len(items) - n})" if len(items) > n else "")


def format_number(x: float) -> str:
    if math.isnan(x):
        return ""
    if x.is_integer() and abs(x) < 1e15:
        return str(int(x))
    return np.format_float_positional(x, trim="-")


def _cell(x: float, symbol: str) -> str:
    return f'"{symbol}"' if symbol else format_number(float(x))


def _json_float(x: float):
    return None if isinstance(x, float) and not math.isfinite(x) else x
