"""Smoke tests: the Streamlit app renders every view without raising."""

from pathlib import Path

import pytest

from pxbuilder import Spec, default_cell, write

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).parents[1] / "src" / "pxcompare" / "app.py")


def run_app(baseline: Path, candidate: Path) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["baseline"] = str(baseline)
    at.session_state["candidate"] = str(candidate)
    at.run()
    assert not at.exception, at.exception
    return at


def test_welcome_page():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("baseline" in m.value for m in at.markdown)


def test_file_pair_with_differences(tmp_path):
    years = ["2020", "2021", "2022", "2023", "2024"]
    base = Spec()
    a = write(tmp_path, "a.px", base)
    b = write(tmp_path, "b.px", Spec(
        values=dict(base.values, Vuosi=years), codes=dict(base.codes, Vuosi=years),
        translations={lang: dict(t, Vuosi=(t["Vuosi"][0], years)) for lang, t in base.translations.items()},
        timeval='TLIST(A1,"2020"-"2024")',
        stub=["Alue", "Sukupuoli", "Tiedot"], heading=["Vuosi"],
        cell=lambda c: '".."' if c["Alue"] == "091" else default_cell(c),
    ))
    at = run_app(a, b)
    assert "Different" in at.error[0].value


def test_equivalent_pair(tmp_path):
    at = run_app(write(tmp_path, "a.px", Spec()), write(tmp_path, "b.px", Spec(keys=True)))
    assert "Equivalent" in at.success[0].value


def test_folders(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    write(tmp_path / "a", "t.px", Spec())
    write(tmp_path / "b", "t.px", Spec(cell=lambda c: "5"))
    write(tmp_path / "a", "only.px", Spec())
    at = run_app(tmp_path / "a", tmp_path / "b")
    assert "2 of 2 tables differ" in at.error[0].value


def test_parse_error_is_shown(tmp_path):
    bad = tmp_path / "bad.px"
    bad.write_text('STUB="x";\n', encoding="utf-8")
    at = run_app(write(tmp_path, "a.px", Spec()), bad)
    assert "Could not read" in at.error[0].value
