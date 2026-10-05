"""Build synthetic PX files for tests.

Cell values come from a function of the cell's value codes, so the same table
can be written with a different value order or STUB/HEADING layout and still
hold the same data.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field, replace


def default_cell(codes: dict[str, str]) -> str:
    if codes["Tiedot"] == "osuus":
        if codes["Alue"] == "049" and codes["Vuosi"] == "2020":
            return '".."'
        base = 10 + len(codes["Alue"]) + int(codes["Sukupuoli"].replace("SSS", "0")) * 1.25
        return f"{base + (int(codes['Vuosi']) - 2020) * 0.5:.2f}".rstrip("0").rstrip(".")
    seed = sum(ord(c) for c in "".join(codes.values()))
    return str(seed * 37 % 9973)


@dataclass
class Spec:
    stub: list[str] = field(default_factory=lambda: ["Alue", "Sukupuoli"])
    heading: list[str] = field(default_factory=lambda: ["Vuosi", "Tiedot"])
    values: dict[str, list[str]] = field(default_factory=lambda: {
        "Alue": ["Koko maa", "Helsinki", "Espoo"],
        "Sukupuoli": ["Yhteensä", "Miehet", "Naiset"],
        "Vuosi": ["2020", "2021", "2022", "2023"],
        "Tiedot": ["Väkiluku", "Osuus (%)"],
    })
    codes: dict[str, list[str]] = field(default_factory=lambda: {
        "Alue": ["SSS", "091", "049"],
        "Sukupuoli": ["SSS", "1", "2"],
        "Vuosi": ["2020", "2021", "2022", "2023"],
        "Tiedot": ["vaki", "osuus"],
    })
    translations: dict[str, dict[str, tuple[str, list[str]]]] = field(default_factory=lambda: {
        "sv": {
            "Alue": ("Område", ["Hela landet", "Helsingfors", "Esbo"]),
            "Sukupuoli": ("Kön", ["Totalt", "Män", "Kvinnor"]),
            "Vuosi": ("År", ["2020", "2021", "2022", "2023"]),
            "Tiedot": ("Uppgifter", ["Folkmängd", "Andel (%)"]),
        },
        "en": {
            "Alue": ("Area", ["Whole country", "Helsinki", "Espoo"]),
            "Sukupuoli": ("Sex", ["Total", "Males", "Females"]),
            "Vuosi": ("Year", ["2020", "2021", "2022", "2023"]),
            "Tiedot": ("Information", ["Population", "Share (%)"]),
        },
    })
    time_var: str = "Vuosi"
    timeval: str = 'TLIST(A1,"2020"-"2023")'
    contvariable: str = "Tiedot"
    header: dict[str, str] = field(default_factory=lambda: {
        "CREATION-DATE": '"20260101 09:00"',
        "TABLEID": '"test_01"',
        "DECIMALS": "2",
        "SHOWDECIMALS": "1",
        "MATRIX": '"test_01"',
        "SUBJECT-AREA": '"Väestö"',
        "COPYRIGHT": "YES",
        "DESCRIPTION": '"Testitaulu: väestö alueittain"',
        "TITLE": '"Väestö muuttujina Alue, Sukupuoli, Vuosi ja Tiedot"',
        "CONTENTS": '"Väestö"',
        "UNITS": '"henkeä"',
    })
    note: str = '"Pitkä huomautus joka jatkuu "\n"seuraavalle riville"'
    cell: object = default_cell
    charset: str = "utf-8"
    bom: bool = False
    keys: bool = False
    values_per_line: int = 8
    extra_lines: list[str] = field(default_factory=list)

    def with_(self, **changes) -> "Spec":
        return replace(self, **changes)


def _q(items: list[str]) -> str:
    return ",".join(f'"{i}"' for i in items)


def build(spec: Spec) -> bytes:
    langs = ["fi"] + list(spec.translations)
    lines = []
    if spec.charset.lower() != "utf-8":
        lines.append('CHARSET="ANSI";')
    lines += [
        'AXIS-VERSION="2013";',
        f'CODEPAGE="{spec.charset}";',
        'LANGUAGE="fi";',
        f"LANGUAGES={_q(langs)};",
    ]
    lines += [f"{k}={v};" for k, v in spec.header.items()]

    def tr(lang: str, var: str) -> tuple[str, list[str]]:
        return spec.translations[lang][var]

    lines.append(f"STUB={_q(spec.stub)};")
    for lang in spec.translations:
        lines.append(f"STUB[{lang}]={_q([tr(lang, v)[0] for v in spec.stub])};")
    lines.append(f"HEADING={_q(spec.heading)};")
    for lang in spec.translations:
        lines.append(f"HEADING[{lang}]={_q([tr(lang, v)[0] for v in spec.heading])};")
    lines.append(f'CONTVARIABLE="{spec.contvariable}";')
    for lang in spec.translations:
        lines.append(f'CONTVARIABLE[{lang}]="{tr(lang, spec.contvariable)[0]}";')

    variables = spec.stub + spec.heading
    for var in variables:
        lines.append(f'VALUES("{var}")={_q(spec.values[var])};')
        for lang in spec.translations:
            name, vals = tr(lang, var)
            lines.append(f'VALUES[{lang}]("{name}")={_q(vals)};')
    if spec.time_var in variables:
        lines.append(f'TIMEVAL("{spec.time_var}")={spec.timeval};')
    for var in variables:
        lines.append(f'CODES("{var}")={_q(spec.codes[var])};')
    if spec.time_var in variables:
        lines.append(f'VARIABLE-TYPE("{spec.time_var}")="Time";')
    lines.append('ELIMINATION("Alue")="Koko maa";')
    for value, unit, precision in zip(spec.values[spec.contvariable], ["henkeä", "prosenttia"], [0, 1]):
        lines.append(f'UNITS("{value}")="{unit}";')
        lines.append(f'LAST-UPDATED("{value}")="20260101 08:00";')
        if precision:
            lines.append(f'PRECISION("{spec.contvariable}","{value}")={precision};')
    for lang in spec.translations:
        name, vals = tr(lang, spec.contvariable)
        for value, unit in zip(vals, ["persons", "per cent"]):
            lines.append(f'UNITS[{lang}]("{value}")="{unit}";')
    lines.append(f"NOTE={spec.note};")
    lines.append('NOTE("Alue")="Alueet vuoden 2026 aluejaon mukaan";')
    lines += spec.extra_lines
    if spec.keys:
        for var in spec.stub:
            lines.append(f'KEYS("{var}")=CODES;')
    lines.append("DATA=")

    stub_cols = [list(zip(spec.codes[v], spec.values[v])) for v in spec.stub]
    head_cols = [list(zip(spec.codes[v], spec.values[v])) for v in spec.heading]
    for stub_combo in itertools.product(*stub_cols):
        row = []
        for head_combo in itertools.product(*head_cols):
            codes = {v: c for v, (c, _) in zip(variables, stub_combo + head_combo)}
            row.append(spec.cell(codes))
        if spec.keys:
            if all(tok == "0" for tok in row):
                continue
            row = [f'"{c}"' for c, _ in stub_combo] + row
        for i in range(0, len(row), spec.values_per_line):
            lines.append(" ".join(row[i:i + spec.values_per_line]))
    lines[-1] += ";"

    text = "\n".join(lines) + "\n"
    data = text.encode(spec.charset)
    return (b"\xef\xbb\xbf" + data) if spec.bom else data


def write(tmp_path, name: str, spec: Spec):
    path = tmp_path / name
    path.write_bytes(build(spec))
    return path
