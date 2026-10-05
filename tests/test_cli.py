import json

from pxbuilder import Spec, default_cell, write
from pxcompare.cli import main
from pxcompare.folder import compare_folders


def changed_cell(codes):
    return "1" if codes["Alue"] == "091" and codes["Vuosi"] == "2021" and codes["Tiedot"] == "vaki" else default_cell(codes)


def test_exit_code_zero_when_equivalent(tmp_path, capsys):
    a = write(tmp_path, "a.px", Spec())
    b = write(tmp_path, "b.px", Spec(values_per_line=3))
    assert main([str(a), str(b)]) == 0
    assert "EQUIVALENT" in capsys.readouterr().out


def test_exit_code_one_and_report_when_different(tmp_path, capsys):
    a = write(tmp_path, "a.px", Spec())
    b = write(tmp_path, "b.px", Spec(cell=changed_cell))
    out_json = tmp_path / "result.json"
    assert main([str(a), str(b), "--lang", "en", "--json", str(out_json)]) == 1
    out = capsys.readouterr().out
    assert "DIFFERENT: Data" in out
    assert "Helsinki" in out and "Population" in out
    result = json.loads(out_json.read_text(encoding="utf-8"))
    assert result["verdict"] == "different"
    assert result["data"]["cells_differing"] == 3


def test_exit_code_two_on_parse_error(tmp_path, capsys):
    a = write(tmp_path, "a.px", Spec())
    bad = tmp_path / "bad.px"
    bad.write_text('STUB="x";\n', encoding="utf-8")
    assert main([str(a), str(bad)]) == 2
    assert "no DATA keyword" in capsys.readouterr().err


def test_ignore_option(tmp_path):
    a = write(tmp_path, "a.px", Spec())
    b = write(tmp_path, "b.px", Spec(note='"Toinen huomautus"'))
    assert main([str(a), str(b)]) == 1
    assert main([str(a), str(b), "--ignore", "note"]) == 0


def test_folder_mode(tmp_path, capsys):
    base, cand = tmp_path / "base", tmp_path / "cand"
    base.mkdir()
    cand.mkdir()
    write(base, "same.px", Spec())
    write(cand, "same.px", Spec())
    write(base, "changed.px", Spec())
    write(cand, "CHANGED.PX", Spec(cell=changed_cell))
    write(base, "gone.px", Spec())
    write(cand, "new.px", Spec())

    result = compare_folders(base, cand)
    verdicts = {e.name: e.verdict for e in result.entries}
    assert verdicts == {
        "changed.px": "different",
        "gone.px": "only in baseline",
        "new.px": "only in candidate",
        "same.px": "identical",
    }
    assert not result.ok

    assert main([str(base), str(cand), "--summary"]) == 1
    out = capsys.readouterr().out
    assert "RESULT: 3 of 4 tables differ" in out


def test_folder_mode_all_match(tmp_path):
    base, cand = tmp_path / "base", tmp_path / "cand"
    base.mkdir()
    cand.mkdir()
    write(base, "t.px", Spec())
    write(cand, "t.px", Spec(keys=True))
    assert main([str(base), str(cand)]) == 0
