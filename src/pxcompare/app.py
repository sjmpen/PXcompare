"""Streamlit app. Start with `pxcompare-app [BASELINE CANDIDATE]`."""

from __future__ import annotations

import difflib
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from pxcompare.compare import (
    DEFAULT_IGNORED_KEYWORDS,
    Comparison,
    CompareOptions,
    Status,
    Verdict,
    compare,
    format_number,
)
from pxcompare.folder import FolderComparison, compare_folders, list_px_files
from pxcompare.parser import PxParseError, parse_px, read_px

ICON = {Status.SAME: "✅", Status.DIFF: "❌", Status.SKIPPED: "➖"}
WORD = {Status.SAME: "OK", Status.DIFF: "Differs", Status.SKIPPED: "Skipped"}
VERDICT_ICON = {"identical": "✅", "equivalent": "✅", "different": "❌", "error": "⚠️",
                "only in baseline": "➖", "only in candidate": "➕"}
BAR = "#2a78d6"
RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
MAX_TABLE_ROWS = 50_000

st.set_page_config(page_title="PX Compare", page_icon="🔍", layout="wide")


# ------------------------------------------------------------------ computation


def _options(key: tuple) -> CompareOptions:
    ignored, abs_tol, rel_tol = key
    return CompareOptions(ignore_keywords=frozenset(ignored), abs_tol=abs_tol, rel_tol=rel_tol)


def _file_sig(path: Path) -> tuple:
    stat = path.stat()
    return (str(path), stat.st_mtime_ns, stat.st_size)


@st.cache_resource(max_entries=16, show_spinner="Comparing…")
def compare_paths(a_sig: tuple, b_sig: tuple, opts: tuple) -> Comparison:
    return compare(read_px(a_sig[0]), read_px(b_sig[0]), _options(opts))


@st.cache_resource(max_entries=16, show_spinner="Comparing…")
def compare_uploads(a_name: str, a: bytes, b_name: str, b: bytes, opts: tuple) -> Comparison:
    return compare(parse_px(a, a_name), parse_px(b, b_name), _options(opts))


@st.cache_resource(max_entries=4, show_spinner="Comparing folders…")
def compare_dirs(a: str, b: str, sig: tuple, opts: tuple, recursive: bool) -> FolderComparison:
    return compare_folders(a, b, _options(opts), recursive=recursive)


# ------------------------------------------------------------------------ inputs


def _clean_path(text: str) -> str:
    # Windows "Copy as path" wraps the path in quotes.
    return text.strip().strip('"').strip("'")


def sidebar() -> tuple:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    st.session_state.setdefault("baseline", args[0] if len(args) > 0 else "")
    st.session_state.setdefault("candidate", args[1] if len(args) > 1 else "")

    with st.sidebar:
        st.header("What to compare")
        source = st.radio("Source", ["Paths", "Upload two files"], horizontal=True, label_visibility="collapsed")
        uploads = None
        if source == "Paths":
            st.text_input("Baseline: file or folder", key="baseline",
                          help="Output of the current (trusted) pipeline.")
            st.text_input("Candidate: file or folder", key="candidate",
                          help="Output of the changed pipeline. Folders are matched by file name.")
            recursive = st.checkbox("Include subfolders", value=False)
        else:
            recursive = False
            up_a = st.file_uploader("Baseline PX file", type=["px"])
            up_b = st.file_uploader("Candidate PX file", type=["px"])
            uploads = (up_a, up_b)

        st.header("Options")
        ignored = st.text_input(
            "Ignored keywords", value=", ".join(sorted(DEFAULT_IGNORED_KEYWORDS)),
            help="Comma-separated. These change on every run, so they are listed but never counted as differences.",
        )
        abs_tol = st.number_input("Absolute tolerance", min_value=0.0, value=0.0, format="%g",
                                  help="0 = numbers must be exactly equal.")
        rel_tol = st.number_input("Relative tolerance", min_value=0.0, value=0.0, format="%g",
                                  help="e.g. 1e-9. 0 = exact.")
    opts = (tuple(sorted({k.strip().upper() for k in ignored.split(",") if k.strip()})), abs_tol, rel_tol)
    return source, uploads, recursive, opts


# ---------------------------------------------------------------------- helpers


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _verdict_banner(cmp: Comparison) -> None:
    v = cmp.verdict
    if v is Verdict.IDENTICAL:
        st.success("**Identical**: the files are byte-for-byte the same.", icon="✅")
    elif v is Verdict.EQUIVALENT:
        st.success("**Equivalent**: same content; only formatting or ignored keywords differ.", icon="✅")
    else:
        st.error("**Different**: " + ", ".join(c.title for c in cmp.failed_checks), icon="❌")


def _checks_table(cmp: Comparison) -> None:
    lines = ["| | Check | Result |", "|---|---|---|"]
    for c in cmp.checks:
        lines.append(f"| {ICON[c.status]} {WORD[c.status]} | **{c.title}** | {_md_escape(c.summary)} |")
    st.markdown("\n".join(lines))
    for hint in cmp.hints:
        st.warning(hint, icon="💡")


def _lang_picker(cmp: Comparison, key: str) -> str | None:
    langs = cmp.baseline.languages
    if len(langs) <= 1:
        return None
    choice = st.segmented_control("Label language", langs, default=cmp.baseline.language, key=key)
    return None if choice in (None, cmp.baseline.language) else choice


# -------------------------------------------------------------------------- tabs


def tab_data(cmp: Comparison, lang: str | None, key: str) -> None:
    d = cmp.data
    if not d.comparable:
        st.info(f"Data was not compared: {d.reason}. See *Variables & values*.", icon="➖")
        return
    only = []
    if d.n_baseline != d.n_common:
        only.append(f"{d.n_baseline - d.n_common:,} cells exist only in the baseline")
    if d.n_candidate != d.n_common:
        only.append(f"{d.n_candidate - d.n_common:,} cells exist only in the candidate")
    if only:
        st.caption("Compared on the cells both files share; " + " and ".join(only) + " (see *Time periods* / *Variables & values*).")
    if not d.n_diff:
        st.success(f"All {d.n_common:,} compared cells match.", icon="✅")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Differing cells", f"{d.n_diff:,}", help=f"of {d.n_common:,} compared cells")
    c1.caption(f"{d.n_diff / d.n_common:.2%} of {d.n_common:,} compared")
    c2.metric("Involving symbols", f"{d.n_symbol_diff:,}", help='Cells where one side is a symbol such as ".." or "-"')
    c3.metric("Largest |difference|", format_number(d.max_abs) if d.n_diff > d.n_symbol_diff else "–")
    c4.metric("Largest relative difference", f"{d.max_rel:.3g} ×" if d.max_rel >= 10 else f"{d.max_rel:.3%}"
              if d.n_diff > d.n_symbol_diff else "–")

    st.subheader("Where are the differences?")
    st.caption("Differing cells per value of each variable. Hover a bar for details.")
    breakdown = [(var, rows) for var, rows in d.breakdown(lang) if len(var.values) > 1]
    cols = st.columns(2)
    for i, (var, rows) in enumerate(breakdown):
        with cols[i % 2]:
            _bar_chart(var.name_in(lang), rows)

    if len(d.variables) >= 2:
        st.subheader("Differences by two variables")
        names = [v.name_in(lang) for v in d.variables]
        time_idx = next((i for i, v in enumerate(d.variables) if v.is_time), len(names) - 1)
        row_default = next(i for i in range(len(names)) if i != time_idx)
        a, b = st.columns(2)
        r = a.selectbox("Rows", range(len(names)), index=row_default, format_func=names.__getitem__, key=f"{key}-hr")
        c = b.selectbox("Columns", range(len(names)), index=time_idx, format_func=names.__getitem__, key=f"{key}-hc")
        if r == c:
            st.info("Pick two different variables.")
        else:
            _heatmap(d.crosstab(r, c, lang), names[r], names[c])

    st.subheader("Differing cells")
    table = d.table(lang=lang, limit=MAX_TABLE_ROWS)
    label_cols = [v.name_in(lang) for v in d.variables]
    filter_cols = [c for c in label_cols if table[c].nunique() > 1]
    if filter_cols:
        with st.expander("Filter", expanded=False):
            fcols = st.columns(min(3, len(filter_cols)))
            for i, col in enumerate(filter_cols):
                options = list(dict.fromkeys(table[col]))
                chosen = fcols[i % len(fcols)].multiselect(col, options, key=f"{key}-f-{i}")
                if chosen:
                    table = table[table[col].isin(chosen)]
    if d.n_diff > MAX_TABLE_ROWS:
        st.caption(f"Showing the first {MAX_TABLE_ROWS:,} of {d.n_diff:,} differing cells; the CSV has them all.")
    st.dataframe(
        table, hide_index=True,
        column_config={
            "Baseline": st.column_config.TextColumn("Baseline", help="Value in the baseline file, exactly as parsed"),
            "Candidate": st.column_config.TextColumn("Candidate"),
            "Difference": st.column_config.NumberColumn("Difference", help="Candidate − baseline", format="%g"),
            "Difference %": st.column_config.NumberColumn("Difference %", help="Relative to the baseline value", format="%.4g %%"),
        },
    )
    csv = d.table(lang=lang).to_csv(index=False).encode("utf-8-sig")
    st.download_button("Download all differing cells (CSV)", csv, file_name=f"{Path(cmp.candidate.name).stem}_differences.csv",
                       mime="text/csv", key=f"{key}-csv")


def _bar_chart(title: str, rows: list[tuple[str, int, int]]) -> None:
    if not rows:
        return
    shown = rows[:25]
    df = pd.DataFrame(shown, columns=["Value", "Differing cells", "Cells per value"])
    df["Share"] = df["Differing cells"] / df["Cells per value"]
    chart = (
        alt.Chart(df, title=alt.Title(title, anchor="start", fontSize=14))
        .mark_bar(color=BAR, cornerRadiusEnd=4, size=14)
        .encode(
            y=alt.Y("Value:N", sort=None, title=None, axis=alt.Axis(labelLimit=240)),
            x=alt.X("Differing cells:Q", title="Differing cells", axis=alt.Axis(tickMinStep=1, format="d", grid=True)),
            tooltip=["Value", "Differing cells", "Cells per value", alt.Tooltip("Share:Q", format=".1%")],
        )
        .properties(height=alt.Step(26))
    )
    st.altair_chart(chart, width="stretch")
    if len(rows) > len(shown):
        st.caption(f"Top {len(shown)} of {len(rows)} values with differences.")


def _heatmap(grid: pd.DataFrame, row_name: str, col_name: str) -> None:
    rows, cols = list(grid.index), list(grid.columns)
    note = ""
    if len(rows) > 40:
        rows = [r for r in rows if grid.loc[r].sum()]
        note += f" Only the {len(rows)} {row_name} values with differences are shown."
    if len(cols) > 80:
        cols = [c for c in cols if grid[c].sum()]
        note += f" Only the {len(cols)} {col_name} values with differences are shown."
    long = grid.loc[rows, cols].stack().reset_index()
    long.columns = ["row", "col", "n"]
    long = long[long["n"] > 0]
    chart = (
        alt.Chart(long)
        .mark_rect(cornerRadius=2)
        .encode(
            x=alt.X("col:N", title=col_name, scale=alt.Scale(domain=cols),
                    axis=alt.Axis(labelLimit=120, labelAngle=-60, labelOverlap=True)),
            y=alt.Y("row:N", title=row_name, scale=alt.Scale(domain=rows), axis=alt.Axis(labelLimit=220)),
            color=alt.Color("n:Q", title="Differing cells", scale=alt.Scale(range=RAMP[1:], domainMin=1),
                            legend=alt.Legend(format="d", tickMinStep=1)),
            tooltip=[alt.Tooltip("row:N", title=row_name), alt.Tooltip("col:N", title=col_name),
                     alt.Tooltip("n:Q", title="Differing cells")],
        )
        .properties(height=alt.Step(24), width=alt.Step(max(16, min(60, 900 // max(1, len(cols))))))
    )
    st.altair_chart(chart, width="content")
    st.caption("Empty squares have no differences." + note)


def tab_time(cmp: Comparison) -> None:
    time = [d for d in cmp.values if d.is_time]
    if not time:
        st.info("Neither file has a time variable (VARIABLE-TYPE=\"Time\" or TIMEVAL).", icon="➖")
        return
    for d in time:
        st.subheader(d.variable)
        va = cmp.baseline.variable(d.variable)
        vb = next(vb for xa, vb in cmp.variables.pairs if xa.name == d.variable)
        c1, c2 = st.columns(2)
        c1.metric("Baseline", d.baseline_range, help=f"{d.n_baseline} periods")
        c1.caption(f"{d.n_baseline} periods" + (f" · TLIST {va.timeval.scale}" if va.timeval else ""))
        c2.metric("Candidate", d.candidate_range, help=f"{d.n_candidate} periods")
        c2.caption(f"{d.n_candidate} periods" + (f" · TLIST {vb.timeval.scale}" if vb.timeval else ""))
        if not d.differs:
            st.success("Same periods in both files.", icon="✅")
            continue
        if d.added:
            st.error(f"**Only in candidate ({len(d.added)}):** " + ", ".join(d.added), icon="➕")
        if d.removed:
            st.error(f"**Only in baseline ({len(d.removed)}):** " + ", ".join(d.removed), icon="➖")
        if d.timeval_note:
            st.error(d.timeval_note, icon="❌")
        _value_details(d, va.values, vb.values, show_added_removed=False)
        if d.added or d.removed:
            _period_strip(va.values, vb.values)


def _period_strip(a: list[str], b: list[str]) -> None:
    periods = sorted(set(a) | set(b))
    rows = [{"Period": p, "File": f, "Present": p in s}
            for p in periods for f, s in (("Baseline", set(a)), ("Candidate", set(b)))]
    df = pd.DataFrame(rows)
    df["Status"] = df["Present"].map({True: "present", False: "missing"})
    chart = (
        alt.Chart(df)
        .mark_rect(cornerRadius=2, stroke="white", strokeWidth=1)
        .encode(
            x=alt.X("Period:N", sort=periods, title=None, axis=alt.Axis(labelAngle=-60, labelOverlap=True)),
            y=alt.Y("File:N", title=None, sort=["Baseline", "Candidate"]),
            color=alt.Color("Status:N", scale=alt.Scale(domain=["present", "missing"], range=[BAR, "#d03b3b"]),
                            legend=alt.Legend(title=None, orient="top")),
            tooltip=["File", "Period", "Status"],
        )
        .properties(height=alt.Step(28))
    )
    st.altair_chart(chart, width="stretch")


def tab_variables(cmp: Comparison) -> None:
    v = cmp.variables
    c1, c2 = st.columns(2)
    for col, title, (stub, heading) in ((c1, "Baseline", v.layout_baseline), (c2, "Candidate", v.layout_candidate)):
        with col.container(border=True):
            st.markdown(f"**{title} layout**")
            st.markdown(f"Stub (rows): {', '.join(f'`{x}`' for x in stub) or '—'}  \nHeading (columns): {', '.join(f'`{x}`' for x in heading) or '—'}")
    if v.layout_changed:
        st.error("The STUB/HEADING layout differs. Data was aligned by variable before comparing.", icon="🔀")
    if v.only_in_baseline:
        st.error("Variables only in baseline: " + ", ".join(v.only_in_baseline), icon="➖")
    if v.only_in_candidate:
        st.error("Variables only in candidate: " + ", ".join(v.only_in_candidate), icon="➕")
    for x, y in v.renamed:
        st.error(f"Variable renamed: `{x}` → `{y}`", icon="✏️")
    if v.translation_changes:
        st.error("Translated variable names differ:", icon="🌐")
        st.dataframe(pd.DataFrame(v.translation_changes, columns=["Variable", "Language", "Baseline", "Candidate"]),
                     hide_index=True)

    rows = []
    for (va, vb), d in zip(v.pairs, cmp.values):
        rows.append({
            "": ICON[Status.DIFF if d.differs else Status.SAME],
            "Variable": va.name,
            "Type": va.var_type or ("Time" if va.is_time else ""),
            "Baseline": f"{va.placement}, {len(va.values)} values",
            "Candidate": f"{vb.placement}, {len(vb.values)} values",
            "Matched by": d.matched_by,
            "Differences": d.describe(),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    for (va, vb), d in zip(v.pairs, cmp.values):
        if d.differs and not d.is_time:
            with st.expander(f"❌ {d.variable}: {d.describe()}", expanded=True):
                _value_details(d, va.values, vb.values)


def _value_details(d, baseline_values: list[str], candidate_values: list[str], show_added_removed: bool = True) -> None:
    if show_added_removed and (d.added or d.removed):
        c1, c2 = st.columns(2)
        c1.markdown(f"**Only in baseline ({len(d.removed)})**")
        c1.dataframe(pd.DataFrame({"Value": d.removed}), hide_index=True)
        c2.markdown(f"**Only in candidate ({len(d.added)})**")
        c2.dataframe(pd.DataFrame({"Value": d.added}), hide_index=True)
    if d.order_changed:
        st.markdown("🔀 **Order of values changed.** Data was aligned by code before comparing.")
        n = max(len(baseline_values), len(candidate_values))
        st.dataframe(pd.DataFrame({
            "Baseline order": baseline_values + [""] * (n - len(baseline_values)),
            "Candidate order": candidate_values + [""] * (n - len(candidate_values)),
        }), hide_index=True)
    if d.relabelled:
        st.markdown("**Relabelled** (same code, different text)")
        st.dataframe(pd.DataFrame(d.relabelled, columns=["Code", "Baseline", "Candidate"]), hide_index=True)
    if d.code_changes:
        st.markdown("**Code changed** (same text, different code)")
        st.dataframe(pd.DataFrame(d.code_changes, columns=["Value", "Baseline code", "Candidate code"]), hide_index=True)
    if d.translation_changes:
        st.markdown("**Translations differ**")
        st.dataframe(pd.DataFrame(d.translation_changes, columns=["Language", "Value", "Baseline", "Candidate"]),
                     hide_index=True)


def tab_metadata(cmp: Comparison, key: str) -> None:
    m = cmp.metadata
    if m.changes:
        st.dataframe(
            pd.DataFrame([{"Keyword": c.label, "Change": c.kind, "Baseline": c.baseline, "Candidate": c.candidate}
                          for c in m.changes]),
            hide_index=True,
        )
    else:
        st.success(f"All {m.n_compared} compared keywords match.", icon="✅")
    if st.toggle("Show every compared keyword side by side", key=f"{key}-allmeta"):
        changed = {id(c) for c in m.changes}
        st.dataframe(
            pd.DataFrame([{"": ICON[Status.DIFF if id(c) in changed else Status.SAME], "Keyword": c.label,
                           "Baseline": c.baseline, "Candidate": c.candidate} for c in m.compared]),
            hide_index=True,
        )
    if m.ignored:
        with st.expander(f"Ignored keywords ({len(m.ignored)}): never counted as differences"):
            st.dataframe(
                pd.DataFrame([{"Keyword": c.label, "Baseline": c.baseline, "Candidate": c.candidate,
                               "Same": c.baseline == c.candidate} for c in m.ignored]),
                hide_index=True,
            )
    st.caption("STUB, HEADING, VALUES, CODES and TIMEVAL are compared under *Variables & values* and *Time periods*.")


def tab_raw(cmp: Comparison) -> None:
    st.caption("Plain text diff of everything before DATA. Line wrapping, keyword order and encoding "
               "show up here even when the content is the same, so use the other tabs for the verdict.")
    diff = list(difflib.unified_diff(cmp.baseline.metadata_text.splitlines(), cmp.candidate.metadata_text.splitlines(),
                                     "baseline", "candidate", n=1, lineterm=""))
    if diff:
        st.code("\n".join(diff), language="diff")
    else:
        st.success("The metadata text is identical.", icon="✅")


def tab_files(cmp: Comparison) -> None:
    rows = []
    for label, get in (
        ("File", lambda f: str(f.path or f.name)),
        ("Encoding", lambda f: f.encoding),
        ("AXIS-VERSION", lambda f: f.get_text("AXIS-VERSION") or "—"),
        ("Languages", lambda f: ", ".join(f.languages) or "—"),
        ("Variables", lambda f: str(len(f.variables))),
        ("Cells", lambda f: f"{f.size:,}"),
        ("Sparse KEYS format", lambda f: "yes" if f.uses_keys else "no"),
        ("SHA-256", lambda f: f.sha256),
        ("Parser warnings", lambda f: "; ".join(f.warnings) or "none"),
    ):
        rows.append({"": label, "Baseline": get(cmp.baseline), "Candidate": get(cmp.candidate)})
    st.dataframe(pd.DataFrame(rows), hide_index=True)


# ------------------------------------------------------------------------ views


def show_comparison(cmp: Comparison, key: str = "single") -> None:
    st.caption(f"**Baseline:** `{cmp.baseline.path or cmp.baseline.name}`  \n**Candidate:** `{cmp.candidate.path or cmp.candidate.name}`")
    _verdict_banner(cmp)
    _checks_table(cmp)
    lang = _lang_picker(cmp, f"{key}-lang")

    status = {c.key: c.status for c in cmp.checks}
    tabs = [
        ("data", "Data", status["data"]),
        ("time", "Time periods", status["time"]),
        ("variables", "Variables & values", Status.DIFF if Status.DIFF in (status["variables"], status["values"]) else Status.SAME),
        ("metadata", "Metadata", status["metadata"]),
        ("raw", "Raw text", None),
        ("files", "File info", None),
    ]
    labels = [f"{ICON[s]} {title}" if s else title for _, title, s in tabs]
    default = next((label for label, (_, _, s) in zip(labels, tabs) if s is Status.DIFF), labels[0])
    try:
        containers = st.tabs(labels, default=default, key=f"{key}-tabs")
    except TypeError:  # Streamlit without default-tab support
        containers = st.tabs(labels)
    for (name, _, _), tab in zip(tabs, containers):
        with tab:
            if name == "data":
                tab_data(cmp, lang, key)
            elif name == "time":
                tab_time(cmp)
            elif name == "variables":
                tab_variables(cmp)
            elif name == "metadata":
                tab_metadata(cmp, key)
            elif name == "raw":
                tab_raw(cmp)
            else:
                tab_files(cmp)


def show_folders(fc: FolderComparison) -> None:
    st.caption(f"**Baseline:** `{fc.baseline}`  \n**Candidate:** `{fc.candidate}`")
    if not fc.entries:
        st.error("No .px files found in either folder.")
        return
    bad = [e for e in fc.entries if not e.ok]
    if bad:
        st.error(f"**{len(bad)} of {len(fc.entries)} tables differ.**", icon="❌")
    else:
        st.success(f"**All {len(fc.entries)} tables match.**", icon="✅")

    keys = [("metadata", "Metadata"), ("variables", "Variables"), ("values", "Values"), ("time", "Time"), ("data", "Data")]
    rows = []
    for e in fc.entries:
        row = {"": VERDICT_ICON.get(e.verdict, ""), "File": e.name, "Verdict": e.verdict}
        for k, title in keys:
            row[title] = ICON[e.comparison.check(k).status] if e.comparison else ""
        row["Data summary"] = e.comparison.check("data").summary if e.comparison else (e.error or "")
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True)

    names = [e.name for e in fc.entries]
    default = names.index(bad[0].name) if bad else 0
    choice = st.selectbox(
        "Details for", range(len(names)), index=default,
        format_func=lambda i: f"{VERDICT_ICON.get(fc.entries[i].verdict, '')} {names[i]}  ({fc.entries[i].verdict})",
    )
    entry = fc.entries[choice]
    st.divider()
    st.subheader(entry.name)
    if entry.comparison:
        show_comparison(entry.comparison, key=f"f{choice}")
    elif entry.error:
        st.error(f"Could not read this file: {entry.error}", icon="⚠️")
    else:
        st.error(f"This file exists {entry.verdict}.", icon="➖")


def show_welcome() -> None:
    st.markdown(
        """
Compare the PX output of two pipeline runs: a **baseline** (current pipeline) and a **candidate** (changed pipeline).

1. Enter two **file** paths, or two **folder** paths (files are paired by name), in the sidebar. Or upload two files.
2. Read the verdict and the checklist. **Equivalent** means the content is the same even if the
   formatting is not (line wrapping, `1.50` vs `1.5`, KEYS vs full data).
3. Open the tab marked ❌ to see exactly what differs.

Nothing leaves your machine: the app runs locally and only listens on `localhost`.
"""
    )


def main() -> None:
    st.title("🔍 PX Compare")
    source, uploads, recursive, opts = sidebar()
    try:
        if source != "Paths":
            up_a, up_b = uploads
            if not (up_a and up_b):
                show_welcome()
                return
            show_comparison(compare_uploads(up_a.name, up_a.getvalue(), up_b.name, up_b.getvalue(), opts))
            return

        a_text, b_text = _clean_path(st.session_state.baseline), _clean_path(st.session_state.candidate)
        if not (a_text and b_text):
            show_welcome()
            return
        a, b = Path(a_text).expanduser(), Path(b_text).expanduser()
        for p, role in ((a, "Baseline"), (b, "Candidate")):
            if not p.exists():
                st.error(f"{role} not found: `{p}`")
                return
        if a.is_dir() and b.is_dir():
            sig = tuple(_file_sig(p) for d in (a, b) for p in list_px_files(d, recursive).values())
            show_folders(compare_dirs(str(a), str(b), sig, opts, recursive))
        elif a.is_file() and b.is_file():
            show_comparison(compare_paths(_file_sig(a), _file_sig(b), opts))
        else:
            st.error("Give two files or two folders, not one of each.")
    except PxParseError as exc:
        st.error(f"Could not read a PX file: {exc}", icon="⚠️")


main()
