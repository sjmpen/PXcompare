"""Plain-text report for the command line (and Databricks job logs)."""

from __future__ import annotations

from .compare import Comparison, Status, Verdict
from .folder import FolderComparison

_COLORS = {"green": "32", "red": "31", "yellow": "33", "dim": "2", "bold": "1"}
_STATUS_LABEL = {Status.SAME: ("  OK  ", "green"), Status.DIFF: (" DIFF ", "red"), Status.SKIPPED: (" SKIP ", "dim")}
_VERDICT_COLOR = {"identical": "green", "equivalent": "green", "different": "red"}


class _Out:
    def __init__(self, color: bool):
        self.color = color
        self.lines: list[str] = []

    def c(self, text: str, color: str) -> str:
        return f"\033[{_COLORS[color]}m{text}\033[0m" if self.color else text

    def __call__(self, line: str = "") -> None:
        self.lines.append(line)

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def render_comparison(cmp: Comparison, max_rows: int = 20, lang: str | None = None, color: bool = False) -> str:
    out = _Out(color)
    _comparison(out, cmp, max_rows, lang)
    return out.text()


def render_folder(result: FolderComparison, max_rows: int = 20, lang: str | None = None,
                  color: bool = False, details: bool = True) -> str:
    out = _Out(color)
    out(out.c("PX compare: folders", "bold"))
    n_a = sum(1 for e in result.entries if e.baseline)
    n_b = sum(1 for e in result.entries if e.candidate)
    out(f"  Baseline : {result.baseline}  ({n_a} PX files)")
    out(f"  Candidate: {result.candidate}  ({n_b} PX files)")
    out()
    if not result.entries:
        out(out.c("No PX files found.", "red"))
        return out.text()

    keys = ["metadata", "variables", "values", "time", "data"]
    header = ["File", "Verdict", "Metadata", "Variables", "Values", "Time", "Data"]
    rows = []
    for e in result.entries:
        if e.comparison:
            marks = [{"same": "ok", "diff": "DIFF", "skipped": "-"}[e.comparison.check(k).status.value] for k in keys]
        else:
            marks = [""] * len(keys)
        rows.append([e.name, e.verdict.upper() if not e.ok else e.verdict] + marks)
    for i, line in enumerate(_table(header, rows)):
        if i >= 2:
            entry = result.entries[i - 2]
            line = out.c(line, "green" if entry.ok else "red")
        out("  " + line)
    out()

    counts = result.counts()
    bad = sum(1 for e in result.entries if not e.ok)
    tally = ", ".join(f"{n} {k}" for k, n in counts.items())
    if bad:
        out(out.c(f"RESULT: {bad} of {len(result.entries)} tables differ ({tally})", "red"))
    else:
        out(out.c(f"RESULT: all {len(result.entries)} tables match ({tally})", "green"))

    if details:
        for e in result.entries:
            if e.ok:
                continue
            out()
            out("=" * 100)
            if e.comparison:
                _comparison(out, e.comparison, max_rows, lang)
            elif e.error:
                out(out.c(f"{e.name}: could not be read: {e.error}", "red"))
            else:
                out(out.c(f"{e.name}: {e.verdict}", "red"))
    return out.text()


def _comparison(out: _Out, cmp: Comparison, max_rows: int, lang: str | None) -> None:
    a, b = cmp.baseline, cmp.candidate
    out(out.c("PX compare", "bold"))
    for role, f in (("Baseline ", a), ("Candidate", b)):
        out(f"  {role}: {f.path or f.name}  ({f.encoding}, {len(f.variables)} variables, {f.size:,} cells)")
    out()
    verdict = cmp.verdict
    out(out.c(cmp.headline.replace(verdict.value.capitalize(), verdict.value.upper(), 1), _VERDICT_COLOR[verdict.value]))
    out()
    width = max(len(c.title) for c in cmp.checks)
    for check in cmp.checks:
        label, color = _STATUS_LABEL[check.status]
        out(f"  [{out.c(label, color)}] {check.title:<{width}}  {check.summary}")
    for hint in cmp.hints:
        out()
        out(out.c(f"  Hint: {hint}", "yellow"))

    if verdict is not Verdict.DIFFERENT:
        return

    if cmp.metadata.changes:
        out()
        out(out.c("Table metadata", "bold"))
        for change in cmp.metadata.changes[:max_rows]:
            out(f"  {change.label}  ({change.kind})")
            out(f"    baseline : {_short(change.baseline)}")
            out(f"    candidate: {_short(change.candidate)}")
        _more(out, len(cmp.metadata.changes), max_rows)

    v = cmp.variables
    if cmp.check("variables").status is Status.DIFF:
        out()
        out(out.c("Variables", "bold"))
        if v.only_in_baseline:
            out(f"  only in baseline : {', '.join(v.only_in_baseline)}")
        if v.only_in_candidate:
            out(f"  only in candidate: {', '.join(v.only_in_candidate)}")
        for x, y in v.renamed:
            out(f"  renamed: {x} → {y}")
        if v.layout_changed:
            out(f"  baseline  layout: stub = {', '.join(v.layout_baseline[0]) or '—'};  heading = {', '.join(v.layout_baseline[1]) or '—'}")
            out(f"  candidate layout: stub = {', '.join(v.layout_candidate[0]) or '—'};  heading = {', '.join(v.layout_candidate[1]) or '—'}")
        for var, l, x, y in v.translation_changes:
            out(f"  name of {var} [{l}]: {x!r} → {y!r}")

    differing = [d for d in cmp.values if d.differs]
    if differing:
        out()
        out(out.c("Values, codes and time periods", "bold"))
        for d in differing:
            out(f"  {d.variable}  (matched by {d.matched_by}; {d.n_baseline} → {d.n_candidate} values"
                + (f"; {d.baseline_range} → {d.candidate_range}" if d.is_time else "") + ")")
            if d.added:
                out(f"    added    : {_list(d.added, max_rows)}")
            if d.removed:
                out(f"    removed  : {_list(d.removed, max_rows)}")
            for code, x, y in d.relabelled[:max_rows]:
                out(f"    relabelled [{code}]: {x!r} → {y!r}")
            for text, x, y in d.code_changes[:max_rows]:
                out(f"    code of {text!r}: {x!r} → {y!r}")
            for l, value, x, y in d.translation_changes[:max_rows]:
                out(f"    [{l}] {value}: {x!r} → {y!r}")
            if d.order_changed:
                out("    order of values changed (data was aligned by code before comparing)")
            if d.timeval_note:
                out(f"    {d.timeval_note}")

    data = cmp.data
    if data.comparable and data.n_diff:
        out()
        shown = min(max_rows, data.n_diff)
        out(out.c(f"Data: {data.n_diff:,} differing cells (showing {shown})", "bold"))
        table = data.table(lang=lang, limit=max_rows)
        header = list(table.columns)
        rows = []
        for record in table.itertuples(index=False):
            *labels, base, cand, delta, rel = record
            rows.append(labels + [base, cand, _num(delta), "" if rel != rel else f"{rel:+.3g} %"])
        for line in _table(header, rows, right_from=len(header) - 4):
            out("  " + line)
        _more(out, data.n_diff, max_rows)
        out()
        out("  Where the differences are (differing cells / cells per value):")
        for var, counts in data.breakdown(lang):
            if len(counts) == len(var.values) and len(set(c for _, c, _ in counts)) == 1:
                out(f"    {var.name_in(lang)}: every value equally ({counts[0][1]} each)")
                continue
            parts = [f"{label} {n}/{total}" for label, n, total in counts[:8]]
            more = f", … (+{len(counts) - 8} values)" if len(counts) > 8 else ""
            out(f"    {var.name_in(lang)}: {', '.join(parts)}{more}")


def _table(header: list[str], rows: list[list[str]], right_from: int | None = None, max_width: int = 40) -> list[str]:
    cells = [[_clip(str(c), max_width) for c in row] for row in [header] + rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(header))]

    def fmt(row):
        parts = []
        for i, c in enumerate(row):
            parts.append(c.rjust(widths[i]) if right_from is not None and i >= right_from else c.ljust(widths[i]))
        return "  ".join(parts).rstrip()

    return [fmt(cells[0]), "  ".join("-" * w for w in widths)] + [fmt(r) for r in cells[1:]]


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _short(text: str | None, width: int = 200) -> str:
    return "(missing)" if text is None else _clip(text, width)


def _list(items: list[str], n: int) -> str:
    shown = ", ".join(items[:n])
    return shown + (f", … (+{len(items) - n})" if len(items) > n else "")


def _num(x: float) -> str:
    if x != x:
        return ""
    if float(x).is_integer():
        return f"{int(x):+,}".replace(",", " ")
    return f"{x:+.6g}"


def _more(out: _Out, total: int, shown: int) -> None:
    if total > shown:
        out(f"  … and {total - shown:,} more (use --max-rows, --json, or the app to see all)")
