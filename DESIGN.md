# Design

## Purpose

The PX file is the final product of the statistics pipeline. When the pipeline changes,
running the old and new pipeline on **the same input data** and comparing their PX output
is the end-to-end test. Therefore any difference is a failure, and the tool's job is to make
differences quick to understand (including "the wrong input data was used").

Decisions:

| Question | Decision |
|---|---|
| Scope | One pair of files, or two folders (≤ 20 files, paired by file name) |
| Numbers | Exact equality after parsing (`1.50` = `1.5`); tolerance is optional and off by default |
| Time periods | Different periods are a failure, but the data is still compared on the shared periods |
| Where it runs | Locally (Streamlit app + CLI). The core has no UI dependency, so the same check can run in a Databricks job |

## Architecture

```
src/pxcompare/
  parser.py   bytes → PxFile (metadata, variables, data cube)
  model.py    PxFile, Variable, MetaEntry, TimeVal
  compare.py  PxFile × PxFile → Comparison (five checks, verdict, hints)
  folder.py   pair files in two folders, compare each
  report.py   plain-text report (CLI, job logs)
  cli.py      `pxcompare`  (exit code 0 / 1 / 2)
  app.py      Streamlit UI (`pxcompare-app`, via launch.py)
```

Only `numpy` and `pandas` are needed for the core; `streamlit` and `altair` for the app.

## Parsing PX

Notes from the example files (`statfin_kttav_pxt_15eu`, `11sl`) and the PX 2013 format:

- **Encoding**: UTF-8 BOM wins; otherwise `CODEPAGE` (e.g. `iso-8859-15` with `CHARSET="ANSI"`);
  then UTF-8, then cp1252.
- **Statements** `KEYWORD[lang]("sub1","sub2")=value;`. Values are comma-separated lists;
  adjacent quoted strings (line continuation) are concatenated. Subkeys may contain `(`/`)`.
- **Languages**: `KEYWORD[sv]` entries use *translated* variable and value names as subkeys,
  so translations are mapped to the default language by position in STUB/HEADING/VALUES.
- **TIMEVAL**: both `TLIST(Q1),"20111","20112",…` and `TLIST(A1,"1995"-"2026")` (range is
  expanded; A1, H1, Q1, M1, W1).
- **DATA**: row-major over STUB then HEADING variables (last heading variable fastest).
  Separators are whitespace and commas. Symbols (`"."`, `".."`, …, `"-"`) are kept per cell.
  `KEYS("var")=VALUES|CODES` sparse rows are supported; omitted rows are zeros.
- Numbers may have trailing zeros stripped (`DECIMALS=5` but `56.6418`), hence numeric
  rather than textual comparison.

## Comparing

1. **Match variables** by default-language name, then `VARIABLECODE`, then any translated name.
2. **Match values** per variable by `CODES` (if both files have unique codes), else by text;
   leftover values with equal text but different codes are matched and reported as code changes.
3. **Data**: reshape both cells into cubes, transpose the candidate to the baseline's variable
   order, take the matched values on each axis, compare element-wise. So reordering and
   STUB/HEADING changes do not create spurious cell differences.
4. **Metadata**: every non-structural keyword, keyed by `(keyword, language, subkeys)` with
   subkeys translated to the baseline's default-language names, so e.g. `UNITS[sv]("…")`
   lines up even if a label changed. `STUB/HEADING/VALUES/CODES/TIMEVAL/KEYS` are covered by
   the variable/value/time checks instead.

Verdict: **identical** if bytes are equal; else **different** if any check differs; else
**equivalent**. Hints flag likely causes (≥ 50 % of cells differ → different input data or
misaligned dimension; different periods → check the input period).

## Possible next steps

- Self-contained HTML report export (to attach to a PR or send to a colleague).
- "Displayed precision" mode: only flag differences visible after rounding to `PRECISION`/`SHOWDECIMALS`.
- Pair folder files by `TABLEID` when file names differ.
