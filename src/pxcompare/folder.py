"""Compare every PX file in a baseline folder with its namesake in a candidate folder."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .compare import Comparison, CompareOptions, compare
from .parser import PxParseError, read_px

ONLY_IN_BASELINE = "only in baseline"
ONLY_IN_CANDIDATE = "only in candidate"
ERROR = "error"


@dataclass
class FolderEntry:
    name: str
    baseline: Path | None
    candidate: Path | None
    comparison: Comparison | None = None
    error: str | None = None

    @property
    def verdict(self) -> str:
        if self.comparison is not None:
            return self.comparison.verdict.value
        if self.error is not None:
            return ERROR
        return ONLY_IN_CANDIDATE if self.baseline is None else ONLY_IN_BASELINE

    @property
    def ok(self) -> bool:
        return self.comparison is not None and self.comparison.ok


@dataclass
class FolderComparison:
    baseline: Path
    candidate: Path
    entries: list[FolderEntry]

    @property
    def ok(self) -> bool:
        return bool(self.entries) and all(e.ok for e in self.entries)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            out[e.verdict] = out.get(e.verdict, 0) + 1
        return out


def list_px_files(folder: Path, recursive: bool = False) -> dict[str, Path]:
    pattern = "**/*" if recursive else "*"
    files = [p for p in folder.glob(pattern) if p.is_file() and p.suffix.lower() == ".px"]
    return {p.relative_to(folder).as_posix().lower(): p for p in sorted(files)}


def compare_folders(
    baseline: str | Path,
    candidate: str | Path,
    options: CompareOptions | None = None,
    recursive: bool = False,
) -> FolderComparison:
    baseline, candidate = Path(baseline), Path(candidate)
    for folder in (baseline, candidate):
        if not folder.is_dir():
            raise NotADirectoryError(folder)
    files_a = list_px_files(baseline, recursive)
    files_b = list_px_files(candidate, recursive)
    entries = []
    for key in sorted(set(files_a) | set(files_b)):
        a, b = files_a.get(key), files_b.get(key)
        entry = FolderEntry(name=(a or b).relative_to(baseline if a else candidate).as_posix(), baseline=a, candidate=b)
        if a and b:
            try:
                entry.comparison = compare(read_px(a), read_px(b), options)
            except (PxParseError, OSError) as exc:
                entry.error = str(exc)
        entries.append(entry)
    return FolderComparison(baseline, candidate, entries)
