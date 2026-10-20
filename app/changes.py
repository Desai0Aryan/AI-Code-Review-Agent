"""Combine a diff with parsed syntax trees to find *which code units* a PR changed."""

from dataclasses import dataclass, field

from app.diff import FileDiff
from app.parsing import CodeUnit, ParsedFile, parse_file


@dataclass
class ChangedUnit:
    path: str
    unit: CodeUnit
    changed_lines: list[int]  # new-file line numbers inside this unit that were added
    removed_nearby: int = 0  # count of removed lines attributed to this unit

    @property
    def key(self) -> str:
        return f"{self.path}::{self.unit.qualified_name}"


@dataclass
class ChangeSet:
    changed_units: list[ChangedUnit] = field(default_factory=list)
    module_level: dict[str, list[int]] = field(default_factory=dict)  # path -> lines outside units
    skipped: dict[str, str] = field(default_factory=dict)  # path -> reason
    parsed: dict[str, ParsedFile] = field(default_factory=dict)  # new-side parse per path


def map_changes(
    diffs: list[FileDiff],
    new_sources: dict[str, str],
    old_sources: dict[str, str] | None = None,
) -> ChangeSet:
    """Map each diff's added/removed lines onto functions, methods and classes.

    new_sources: path -> file contents at the PR head.
    old_sources: path -> file contents at the base (used to attribute removed lines).
    """
    old_sources = old_sources or {}
    result = ChangeSet()

    for fd in diffs:
        path = fd.path
        if fd.is_binary:
            result.skipped[path] = "binary file"
            continue
        if fd.is_deleted:
            result.skipped[path] = "file deleted"
            continue
        if path not in new_sources:
            result.skipped[path] = "source unavailable"
            continue

        parsed = parse_file(path, new_sources[path])
        if parsed is None:
            result.skipped[path] = "unsupported language"
            continue
        result.parsed[path] = parsed

        per_unit: dict[str, ChangedUnit] = {}
        loose: list[int] = []

        for line in fd.added_lines:
            unit = parsed.unit_at(line)
            if unit is None:
                loose.append(line)
                continue
            cu = per_unit.setdefault(
                unit.qualified_name, ChangedUnit(path=path, unit=unit, changed_lines=[])
            )
            cu.changed_lines.append(line)

        # Removed lines live in the old file; attribute them via the old tree when we can.
        if fd.removed_lines and path in old_sources and not fd.is_new:
            old_parsed = parse_file(path, old_sources[path])
            if old_parsed is not None:
                for line in fd.removed_lines:
                    old_unit = old_parsed.unit_at(line)
                    if old_unit is None:
                        continue
                    # Match to the new-side unit with the same qualified name, if it survived.
                    new_unit = next(
                        (u for u in parsed.units if u.qualified_name == old_unit.qualified_name),
                        None,
                    )
                    if new_unit is None:
                        continue
                    cu = per_unit.setdefault(
                        new_unit.qualified_name,
                        ChangedUnit(path=path, unit=new_unit, changed_lines=[]),
                    )
                    cu.removed_nearby += 1

        result.changed_units.extend(per_unit.values())
        if loose:
            result.module_level[path] = loose

    result.changed_units.sort(key=lambda c: (c.path, c.unit.start_line))
    return result
