import numpy as np
import pytest

from pxbuilder import Spec, build
from pxcompare.parser import PxParseError, _expand_periods, parse_px


def test_parses_variables_values_and_translations():
    px = parse_px(build(Spec()))
    assert px.language == "fi"
    assert px.languages == ["fi", "sv", "en"]
    assert [v.name for v in px.stub] == ["Alue", "Sukupuoli"]
    assert [v.name for v in px.heading] == ["Vuosi", "Tiedot"]
    assert px.shape == (3, 3, 4, 2)
    alue = px.variable("Alue")
    assert alue.codes == ["SSS", "091", "049"]
    assert alue.names_by_lang == {"sv": "Område", "en": "Area"}
    assert alue.values_in("sv") == ["Hela landet", "Helsingfors", "Esbo"]
    assert px.variable("Tiedot").is_contents
    assert px.variable("Vuosi").is_time
    assert px.variable("Vuosi").timeval.periods == ["2020", "2021", "2022", "2023"]


def test_long_strings_are_concatenated_and_lists_split():
    px = parse_px(build(Spec()))
    assert px.get_text("NOTE") == "Pitkä huomautus joka jatkuu seuraavalle riville"
    assert px.get("VALUES", None, ("Tiedot",)).items == ["Väkiluku", "Osuus (%)"]
    # subkey containing parentheses
    assert px.get_text("UNITS", None, ("Osuus (%)",)) == "prosenttia"
    assert px.get_text("PRECISION", None, ("Tiedot", "Osuus (%)")) == "1"


def test_data_values_and_symbols():
    px = parse_px(build(Spec()))
    assert px.size == 72
    cube_num = px.numbers.reshape(px.shape)
    cube_sym = px.symbols.reshape(px.shape)
    # Espoo (index 2), total sex, 2020, share -> ".."
    assert cube_sym[2, 0, 0, 1] == ".."
    assert np.isnan(cube_num[2, 0, 0, 1])
    assert cube_num[1, 1, 1, 1] == pytest.approx(10 + 3 + 1.25 + 0.5)
    assert (px.symbols == "").sum() == 72 - 3


@pytest.mark.parametrize("charset", ["iso-8859-15", "cp1252"])
def test_ansi_codepages(charset):
    px = parse_px(build(Spec(charset=charset)))
    assert px.variable("Sukupuoli").values[0] == "Yhteensä"
    assert px.get_text("SUBJECT-AREA") == "Väestö"


def test_utf8_bom():
    px = parse_px(build(Spec(bom=True)))
    assert px.encoding == "utf-8 (BOM)"
    assert px.get_text("AXIS-VERSION") == "2013"


def test_timeval_list_form():
    spec = Spec(timeval='TLIST(A1),"2020","2021","2022","2023"')
    assert parse_px(build(spec)).variable("Vuosi").timeval.periods == ["2020", "2021", "2022", "2023"]


def test_period_expansion():
    assert _expand_periods("Q1", "20233", "20242") == ["20233", "20234", "20241", "20242"]
    assert _expand_periods("M1", "202311", "202402") == ["202311", "202312", "202401", "202402"]
    assert _expand_periods("H1", "20231", "20242") == ["20231", "20232", "20241", "20242"]
    assert _expand_periods("W1", "202052", "202101") == ["202052", "202053", "202101"]  # 2020 has 53 ISO weeks


def test_keys_format_matches_full_format():
    def sparse_cell(codes):
        return "0" if codes["Alue"] == "091" else "7"

    full = parse_px(build(Spec(cell=sparse_cell)))
    keyed = parse_px(build(Spec(cell=sparse_cell, keys=True)))
    assert keyed.uses_keys
    np.testing.assert_array_equal(full.numbers, keyed.numbers)


def test_wrong_cell_count_is_reported():
    text = build(Spec()).decode().rstrip().rstrip(";") + " 1 2 3;\n"
    with pytest.raises(PxParseError, match="DATA has 75 cells but the variables define 72"):
        parse_px(text.encode())


def test_missing_data_keyword():
    with pytest.raises(PxParseError, match="no DATA keyword"):
        parse_px(b'STUB="a";\nVALUES("a")="x";\n')


def test_language_suffix_for_default_language_is_folded():
    raw = build(Spec(extra_lines=['SOURCE[fi]="Tilastokeskus";']))
    assert parse_px(raw).get_text("SOURCE") == "Tilastokeskus"
