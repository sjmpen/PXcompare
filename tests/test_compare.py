import pytest

from pxbuilder import Spec, build, default_cell
from pxcompare.compare import CompareOptions, Status, Verdict, compare
from pxcompare.parser import parse_px


def run(a: Spec, b: Spec, **options):
    return compare(parse_px(build(a), "a.px"), parse_px(build(b), "b.px"), CompareOptions(**options))


def statuses(result):
    return {c.key: c.status for c in result.checks}


def failing(result):
    return {c.key for c in result.checks if c.status is Status.DIFF}


def test_identical_bytes():
    r = run(Spec(), Spec())
    assert r.verdict is Verdict.IDENTICAL
    assert r.ok
    assert failing(r) == set()


def test_formatting_only_is_equivalent():
    def trailing_zeros(codes):
        value = default_cell(codes)
        return value + "0" if "." in value and not value.startswith('"') else value

    r = run(Spec(), Spec(values_per_line=5, cell=trailing_zeros))
    assert r.verdict is Verdict.EQUIVALENT
    assert r.check("data").summary == "all 72 compared cells match"


def test_ignored_keyword_is_equivalent_and_listed():
    header = dict(Spec().header, **{"CREATION-DATE": '"20261005 10:00"'})
    r = run(Spec(), Spec(header=header))
    assert r.verdict is Verdict.EQUIVALENT
    ignored = {m.label: (m.baseline, m.candidate) for m in r.metadata.ignored}
    assert ignored["CREATION-DATE"] == ("20260101 09:00", "20261005 10:00")


def test_ignored_keyword_can_be_compared():
    header = dict(Spec().header, **{"CREATION-DATE": '"20261005 10:00"'})
    r = run(Spec(), Spec(header=header), ignore_keywords=frozenset())
    assert failing(r) == {"metadata"}


def test_keys_format_is_equivalent():
    r = run(Spec(), Spec(keys=True))
    assert r.verdict is Verdict.EQUIVALENT


def test_encoding_change_is_a_metadata_difference_only():
    r = run(Spec(), Spec(charset="iso-8859-15"))
    assert failing(r) == {"metadata"}
    assert {m.label for m in r.metadata.changes} == {"CODEPAGE", "CHARSET"}


def test_one_changed_cell():
    def cell(codes):
        if codes == {"Alue": "091", "Sukupuoli": "2", "Vuosi": "2023", "Tiedot": "vaki"}:
            return "1"
        return default_cell(codes)

    r = run(Spec(), Spec(cell=cell))
    assert r.verdict is Verdict.DIFFERENT
    assert failing(r) == {"data"}
    assert r.data.n_diff == 1
    row = r.data.table().iloc[0]
    assert (row["Alue"], row["Sukupuoli"], row["Vuosi"], row["Tiedot"]) == ("Helsinki", "Naiset", "2023", "Väkiluku")
    assert row["Candidate"] == "1"
    assert r.data.table(lang="en").columns[:4].tolist() == ["Area", "Sex", "Year", "Information"]
    breakdown = dict((v.name, rows) for v, rows in r.data.breakdown())
    assert breakdown["Vuosi"] == [("2023", 1, 18)]


def test_number_replaced_by_symbol():
    def cell(codes):
        if codes == {"Alue": "SSS", "Sukupuoli": "SSS", "Vuosi": "2021", "Tiedot": "vaki"}:
            return '".."'
        return default_cell(codes)

    r = run(Spec(), Spec(cell=cell))
    assert r.data.n_diff == 1 and r.data.n_symbol_diff == 1
    assert r.data.table()["Candidate"].iloc[0] == '".."'


def test_tolerance():
    def cell(codes):
        value = default_cell(codes)
        return str(float(value) + 0.001) if codes["Tiedot"] == "vaki" else value

    assert run(Spec(), Spec(cell=cell)).data.n_diff == 36
    assert run(Spec(), Spec(cell=cell), abs_tol=0.01).data.n_diff == 0


def test_reordered_values_are_aligned():
    base = Spec()
    values = dict(base.values, Alue=["Koko maa", "Espoo", "Helsinki"])
    codes = dict(base.codes, Alue=["SSS", "049", "091"])
    translations = {
        lang: dict(t, Alue=(t["Alue"][0], [t["Alue"][1][i] for i in (0, 2, 1)]))
        for lang, t in base.translations.items()
    }
    r = run(base, Spec(values=values, codes=codes, translations=translations))
    assert failing(r) == {"values"}
    assert r.check("data").status is Status.SAME
    assert "order changed" in r.check("values").summary


def test_layout_change_is_aligned():
    r = run(Spec(), Spec(stub=["Alue", "Sukupuoli", "Tiedot"], heading=["Vuosi"]))
    assert failing(r) == {"variables"}
    assert "layout" in r.check("variables").summary
    assert r.check("data").status is Status.SAME


def test_extra_period_in_candidate():
    base = Spec()
    years = ["2020", "2021", "2022", "2023", "2024"]
    values = dict(base.values, Vuosi=years)
    codes = dict(base.codes, Vuosi=years)
    translations = {lang: dict(t, Vuosi=(t["Vuosi"][0], years)) for lang, t in base.translations.items()}
    r = run(base, Spec(values=values, codes=codes, translations=translations, timeval='TLIST(A1,"2020"-"2024")'))
    assert failing(r) == {"time"}
    assert "2020–2023 (4) → 2020–2024 (5)" in r.check("time").summary
    assert "1 added (2024)" in r.check("time").summary
    assert r.data.n_common == 72 and r.data.n_candidate == 90 and r.data.n_diff == 0
    assert any("time periods differ" in h for h in r.hints)


def test_relabelled_value():
    base = Spec()
    values = dict(base.values, Sukupuoli=["Kaikki", "Miehet", "Naiset"])
    r = run(base, Spec(values=values))
    assert failing(r) == {"values"}
    sukupuoli = next(v for v in r.values if v.variable == "Sukupuoli")
    assert sukupuoli.relabelled == [("SSS", "Yhteensä", "Kaikki")]
    assert r.check("data").status is Status.SAME


def test_translation_change():
    base = Spec()
    sv = dict(base.translations["sv"], Sukupuoli=("Kön", ["Totalt", "Män", "Kvinnorna"]))
    r = run(base, Spec(translations=dict(base.translations, sv=sv)))
    assert failing(r) == {"values"}
    sukupuoli = next(v for v in r.values if v.variable == "Sukupuoli")
    assert sukupuoli.translation_changes == [("sv", "Naiset", "Kvinnor", "Kvinnorna")]


def test_translated_metadata_change():
    base = Spec()
    tiedot = base.translations["sv"]["Tiedot"]
    sv = dict(base.translations["sv"], Tiedot=(tiedot[0], ["Befolkning", "Andel (%)"]))
    r = run(base, Spec(translations=dict(base.translations, sv=sv)))
    # The sv label of a content value changed; its UNITS[sv] entry still lines up.
    assert failing(r) == {"values"}


def test_metadata_value_change():
    header = dict(Spec().header, TITLE='"Väestö muuttujina Alue ja Vuosi"')
    r = run(Spec(), Spec(header=header))
    assert failing(r) == {"metadata"}
    (change,) = r.metadata.changes
    assert change.label == "TITLE" and change.kind == "changed"


def test_missing_variable_skips_data():
    base = Spec()
    r = run(base, Spec(stub=["Alue"], cell=lambda codes: default_cell({**codes, "Sukupuoli": "SSS"})))
    assert "variables" in failing(r)
    assert r.check("data").status is Status.SKIPPED


def test_mostly_different_data_gives_hint():
    r = run(Spec(), Spec(cell=lambda codes: "1"))
    assert r.data.n_diff > 36
    assert any("same input data" in h for h in r.hints)


def test_to_dict_is_json_serialisable():
    import json

    r = run(Spec(), Spec(cell=lambda codes: '"-"' if codes["Alue"] == "SSS" else default_cell(codes)))
    json.dumps(r.to_dict())


@pytest.mark.parametrize("swap", [True, False])
def test_crosstab_counts(swap):
    r = run(Spec(), Spec(cell=lambda codes: "1" if codes["Vuosi"] == "2022" else default_cell(codes)))
    rows, cols = (2, 3) if swap else (3, 2)
    table = r.data.crosstab(rows, cols)
    assert table.values.sum() == r.data.n_diff
