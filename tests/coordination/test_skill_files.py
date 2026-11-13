"""The coordination skill files match what the CLI names and the version it prints."""

from __future__ import annotations

import re
from pathlib import Path

from llm_bus.cli.guidance import GUIDANCE_VERSION
from llm_bus.cli.guidance_files import ALL_GUIDANCE_FILES

REFERENCES = Path(__file__).resolve().parents[2] / "skills" / "llm-bus" / "references"
FILES = REFERENCES / "coordination"
MARKER = re.compile(r"<!-- llm-bus guidance-version: (\d+) -->")


def test_every_guidance_file_exists() -> None:
    assert [name for name in ALL_GUIDANCE_FILES if not (FILES / name).is_file()] == []


def test_core_marker_equals_guidance_version_and_is_unique() -> None:
    first_line = (FILES / "core.md").read_text(encoding="utf-8").splitlines()[0]
    match = MARKER.fullmatch(first_line)
    assert match is not None
    assert int(match.group(1)) == GUIDANCE_VERSION
    others = [path.name for path in FILES.glob("*.md") if MARKER.search(path.read_text("utf-8"))]
    assert others == ["core.md"]


def test_index_lists_each_file_exactly_once_and_the_directory_holds_no_other() -> None:
    index = (REFERENCES / "coordination.md").read_text(encoding="utf-8")
    listed = re.findall(r"^\| \[coordination/([\w-]+\.md)\]", index, flags=re.MULTILINE)
    assert sorted(listed) == sorted(set(listed))
    assert sorted(listed) == sorted(path.name for path in FILES.glob("*"))
    assert sorted(listed) == sorted(ALL_GUIDANCE_FILES)
