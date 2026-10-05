# PXcompare

End-to-end regression test for a statistics pipeline: compare the PX files produced by
the **current** pipeline (baseline) with those produced by the **changed** pipeline
(candidate), and explain every difference.

The comparison is *semantic*: both files are parsed and then compared, so formatting
that does not change the content (line wrapping, `1.50` vs `1.5`, keyword order,
sparse `KEYS` vs full data) is not reported as a difference. A reordered dimension
or a variable moved from STUB to HEADING is reported once, and the data is still
compared cell by cell after aligning by codes.

Each comparison reports five checks and a verdict:

| Check | What is compared |
|---|---|
| **Table metadata** | Every keyword in every language (TITLE, UNITS, NOTE, PRECISION, CODEPAGE …), except the ignored ones |
| **Variables** | Which variables exist, STUB/HEADING layout and order, translated variable names |
| **Values & codes** | Values added/removed, relabelled (same code, new text), code changes, translations, order |
| **Time periods** | Period range and list of the time variable, TIMEVAL / TLIST scale |
| **Data** | Every cell both files share: numbers exactly (or within a tolerance) and symbols such as `".."` |

Verdicts: **identical** (byte-for-byte) · **equivalent** (same content, formatting differs) ·
**different**. By default `CREATION-DATE`, `LAST-UPDATED` and `NEXT-UPDATE` are
ignored because they change on every run; they are still listed.

## Install

Python 3.10+.

```bash
git clone https://github.com/sjmpen/PXcompare.git
cd PXcompare
pip install -e ".[app]"
```

Without the app (e.g. on Databricks): `pip install "git+https://github.com/sjmpen/PXcompare.git"`.

## The app

```bash
pxcompare-app                                   # enter paths in the sidebar
pxcompare-app old_run/ new_run/                 # two folders: files are paired by name
pxcompare-app old_run/15eu.px new_run/15eu.px   # two files
```

It opens in your browser, runs only on `localhost`, and sends no usage statistics.

- **Folder view**: one row per table with ✅/❌ per check, then details for the selected table.
- **Verdict and checklist**: what differs, in one line per check.
- **Data tab**: number of differing cells, *where* they are (a time-ordered chart of every
  period first, then per variable, and a heatmap by two variables), and a filterable table of every differing cell with both values and the
  difference. Labels can be shown in any of the file's languages. CSV download.
- **Time periods, Variables & values, Metadata** tabs: side-by-side details.
- **Raw text** tab: plain text diff of the metadata part, for when you want to see the file itself.

## Command line

```bash
pxcompare baseline.px candidate.px
pxcompare old_run/ new_run/            # folder mode
pxcompare old_run/ new_run/ --summary  # only the table of files
pxcompare a.px b.px --lang en --json result.json
```

Exit code **0** = identical or equivalent, **1** = differences found, **2** = a file could
not be read. Options: `--ignore KEYWORD` (repeatable), `--no-default-ignores`,
`--abs-tol`, `--rel-tol`, `--max-rows`, `--recursive`. See `pxcompare --help`.

Example output:

```
DIFFERENT: Table metadata, Data

  [ DIFF ] Table metadata  1 difference (1 changed): TITLE[en] (ignored: CREATION-DATE, LAST-UPDATED)
  [  OK  ] Variables       4 variables, same layout [stub: Ajoneuvon käyttö, Maa-aineskuljetus | heading: Vuosineljännes, Tiedot]
  [  OK  ] Values & codes  all 9 values match in 3 variables
  [  OK  ] Time periods    Vuosineljännes: 2011Q1–2025Q4 (60 periods) in both
  [ DIFF ] Data            2 of 1,620 compared cells differ (0.1 %), max |Δ| 55, 1 with symbols such as ".."
...
  Where the differences are (differing cells / cells per value):
    Vuosineljännes: 2025Q4 2/27
```

## From Python / a Databricks job

```python
from pxcompare import compare_folders
from pxcompare.report import render_folder

result = compare_folders("/Volumes/stats/pipeline/baseline", "/Volumes/stats/pipeline/candidate")
print(render_folder(result))
assert result.ok, "PX output changed, see the report above"
```

`compare_files(a, b)` does the same for one pair; the result has `.verdict`, `.checks`,
`.data.table(lang="en")` (a pandas DataFrame of differing cells) and `.to_dict()`.

## Development

```bash
pip install -e ".[app,dev]"
pytest
```

Tests build synthetic PX files (`tests/pxbuilder.py`) covering both encodings
(UTF-8 with/without BOM, ISO-8859-15/cp1252 "ANSI"), both TIMEVAL forms, KEYS, symbols,
multilingual metadata, reordering and layout changes. See [DESIGN.md](DESIGN.md) for how
the comparison works.
