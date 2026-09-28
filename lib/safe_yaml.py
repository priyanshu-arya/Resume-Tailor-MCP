"""Hardened YAML loading for every file the app reads (spec §9).

`yaml.safe_load` already refuses to construct Python objects, but on its
own it still accepts two denial-of-service shapes: alias expansion
("billion laughs") and nesting deep enough to hit Python's recursion
limit. Resume data never needs anchors/aliases, so they are rejected
outright, and depth is counted while composing -- before any expansion.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from lib.errors import ResumeTailorError

MAX_YAML_BYTES = 2 * 1024 * 1024
MAX_YAML_DEPTH = 20


class _GuardedLoader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self._depth = 0

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ResumeTailorError("YAML_UNSAFE", "YAML anchors/aliases are not allowed.")
        if self.check_event(yaml.MappingStartEvent, yaml.SequenceStartEvent):
            event = self.peek_event()
            if getattr(event, "anchor", None):
                raise ResumeTailorError("YAML_UNSAFE", "YAML anchors/aliases are not allowed.")
        self._depth += 1
        try:
            if self._depth > MAX_YAML_DEPTH + 1:
                raise ResumeTailorError("YAML_TOO_DEEP", f"Document nesting exceeds {MAX_YAML_DEPTH} levels.")
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


def load_text(text: str):
    if len(text.encode("utf-8")) > MAX_YAML_BYTES:
        raise ResumeTailorError("YAML_TOO_LARGE", f"YAML exceeds {MAX_YAML_BYTES} bytes.")
    try:
        return yaml.load(text, Loader=_GuardedLoader)  # noqa: S506 - _GuardedLoader is a SafeLoader
    except ResumeTailorError:
        raise
    except RecursionError:
        raise ResumeTailorError("YAML_TOO_DEEP", f"Document nesting exceeds {MAX_YAML_DEPTH} levels.") from None
    except yaml.YAMLError as e:
        raise ResumeTailorError("YAML_UNSAFE", "Malformed or unsafe YAML (only plain data is allowed).",
                                details={"error_type": type(e).__name__}) from None


def load_file(path: Path):
    path = Path(path)
    if not path.exists():
        return None
    if path.stat().st_size > MAX_YAML_BYTES:
        raise ResumeTailorError("YAML_TOO_LARGE", f"YAML file exceeds {MAX_YAML_BYTES} bytes.")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ResumeTailorError("YAML_UNSAFE", "File is not valid UTF-8.",
                                details={"error_type": "UnicodeDecodeError"}) from None
    return load_text(text)
