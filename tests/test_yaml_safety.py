"""Safe YAML: no code execution, size and depth limits, no data echo in errors."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from lib import storage
from lib.errors import ResumeTailorError

REPO_ROOT = Path(__file__).resolve().parent.parent
PY = str(REPO_ROOT / ".venv" / "bin" / "python")
if not Path(PY).exists():
    PY = sys.executable


def _code(excinfo) -> str:
    return excinfo.value.code


def _payloads(marker: Path) -> list[str]:
    return [
        f"!!python/object/apply:os.system ['touch {marker}']\n",
        f"name: !!python/object/apply:os.system ['touch {marker}']\n",
        f"x: !!python/object/apply:subprocess.call [['touch', '{marker}']]\n",
        f"x: !!python/object/new:os.system ['touch {marker}']\n",
        "x: !!python/name:os.system\n",
        "x: !!python/module:os\n",
        f"x: !!python/object:builtins.dict {{a: 1}}\n",
    ]


def _nested(depth: int):
    node = "leaf"
    for _ in range(depth - 1):
        node = {"k": node}
    return node


def _nested_yaml(depth: int) -> str:
    return storage.dump_yaml(_nested(depth))


# --------------------------------------------------------------------------
# Code execution
# --------------------------------------------------------------------------

def test_python_tags_in_text_rejected_and_not_executed(tmp_path):
    marker = tmp_path / "pwned"
    for payload in _payloads(marker):
        with pytest.raises(ResumeTailorError) as ei:
            storage.yaml_load_text(payload)
        assert _code(ei) == "YAML_UNSAFE", payload
    assert not marker.exists()


def test_python_tag_in_master_file_rejected_and_not_executed(workspace, tmp_path):
    marker = tmp_path / "pwned-master"
    workspace.master_path("resume").write_text(
        "name: Alex\n"
        f"summary: !!python/object/apply:os.system ['touch {marker}']\n"
        "metadata: {kind: resume}\n", encoding="utf-8")
    with pytest.raises(ResumeTailorError) as ei:
        storage.load_master("resume", workspace)
    assert _code(ei) == "YAML_UNSAFE"
    assert not marker.exists()


def test_python_tag_in_version_and_jd_files(workspace, tmp_path):
    marker = tmp_path / "pwned-v"
    evil = f"metadata: !!python/object/apply:os.system ['touch {marker}']\n"
    (workspace.versions_dir / "v1.yaml").write_text(evil, encoding="utf-8")
    (workspace.jd_dir / "j1.yaml").write_text(evil, encoding="utf-8")
    for call in (lambda: storage.load_version("v1", workspace), lambda: storage.load_jd("j1", workspace)):
        with pytest.raises(ResumeTailorError) as ei:
            call()
        assert _code(ei) == "YAML_UNSAFE"
    assert not marker.exists()


def test_save_master_over_unsafe_file_refuses(workspace, legacy_master, tmp_path):
    """An unsafe file on disk must not be silently overwritten nor executed."""
    from lib.ids import normalize_master
    marker = tmp_path / "pwned-save"
    path = workspace.master_path("resume")
    evil = f"summary: !!python/object/apply:os.system ['touch {marker}']\n"
    path.write_text(evil, encoding="utf-8")
    with pytest.raises(ResumeTailorError) as ei:
        storage.save_master("resume", normalize_master(legacy_master, "resume"), None, "x", workspace)
    assert _code(ei) == "YAML_UNSAFE"
    assert path.read_text(encoding="utf-8") == evil
    assert not marker.exists()


# --------------------------------------------------------------------------
# Malformed
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "a: [1, 2\n",
    "a: {b: 1\n",
    "a: b: c\n",
    "- a\nb: 1\n",
    "\ta: 1\n",
    "a: *undefined_alias\n",
    "key: 'unterminated\n",
    "%YAML 9.9\n---\na: 1\n",
])
def test_malformed_yaml(text):
    with pytest.raises(ResumeTailorError) as ei:
        storage.yaml_load_text(text)
    assert _code(ei) == "YAML_UNSAFE"


def test_malformed_master_file(workspace):
    workspace.master_path("resume").write_text("name: [unclosed\n", encoding="utf-8")
    with pytest.raises(ResumeTailorError) as ei:
        storage.load_master("resume", workspace)
    assert _code(ei) == "YAML_UNSAFE"


def test_non_utf8_master_file_is_yaml_unsafe(workspace):
    workspace.master_path("resume").write_bytes(b"name: \xff\xfe\x00bad\n")
    with pytest.raises(ResumeTailorError) as ei:
        storage.load_master("resume", workspace)
    assert _code(ei) == "YAML_UNSAFE"


def test_plain_data_round_trips():
    assert storage.yaml_load_text("a: 1\nb: [x, y]\nc: {d: null}\n") == {"a": 1, "b": ["x", "y"], "c": {"d": None}}
    assert storage.yaml_load_text("") is None


# --------------------------------------------------------------------------
# Size
# --------------------------------------------------------------------------

def test_text_over_2mb_rejected():
    text = "a: '" + "x" * (storage.MAX_YAML_BYTES + 1) + "'\n"
    with pytest.raises(ResumeTailorError) as ei:
        storage.yaml_load_text(text)
    assert _code(ei) == "YAML_TOO_LARGE"


def test_multibyte_text_measured_in_bytes():
    # ~1.1M characters but ~3.3 MB of UTF-8
    text = "a: '" + "\u20ac" * 1_100_000 + "'\n"
    assert len(text) < storage.MAX_YAML_BYTES
    with pytest.raises(ResumeTailorError) as ei:
        storage.yaml_load_text(text)
    assert _code(ei) == "YAML_TOO_LARGE"


def test_file_over_2mb_rejected(workspace):
    path = workspace.master_path("resume")
    path.write_bytes(b"summary: '" + b"x" * (storage.MAX_YAML_BYTES + 10) + b"'\n")
    with pytest.raises(ResumeTailorError) as ei:
        storage.load_master("resume", workspace)
    assert _code(ei) == "YAML_TOO_LARGE"


def test_just_under_limit_ok():
    body = "x" * (storage.MAX_YAML_BYTES - 20)
    assert storage.yaml_load_text(f"a: {body}\n") == {"a": body}


# --------------------------------------------------------------------------
# Depth
# --------------------------------------------------------------------------

def test_depth_25_rejected():
    with pytest.raises(ResumeTailorError) as ei:
        storage.yaml_load_text(_nested_yaml(25))
    assert _code(ei) == "YAML_TOO_DEEP"


def test_depth_25_lists_rejected():
    text = "[" * 25 + "1" + "]" * 25
    with pytest.raises(ResumeTailorError) as ei:
        storage.yaml_load_text(text)
    assert _code(ei) == "YAML_TOO_DEEP"


def test_depth_at_limit_ok():
    assert storage.yaml_load_text(_nested_yaml(storage.MAX_YAML_DEPTH)) == _nested(storage.MAX_YAML_DEPTH)


def test_depth_one_over_limit_rejected():
    with pytest.raises(ResumeTailorError) as ei:
        storage.yaml_load_text(_nested_yaml(storage.MAX_YAML_DEPTH + 1))
    assert _code(ei) == "YAML_TOO_DEEP"


def test_deep_master_file_rejected(workspace):
    workspace.master_path("resume").write_text(_nested_yaml(25), encoding="utf-8")
    with pytest.raises(ResumeTailorError) as ei:
        storage.load_master("resume", workspace)
    assert _code(ei) == "YAML_TOO_DEEP"


def test_extreme_nesting_is_rejected_not_recursion_error():
    """A small (<50 KB) document nested thousands of levels deep must be a
    controlled YAML error, not an uncaught RecursionError from the parser."""
    n = 5000
    text = "[" * n + "]" * n
    assert len(text) < storage.MAX_YAML_BYTES
    with pytest.raises(ResumeTailorError) as ei:
        storage.yaml_load_text(text)
    assert _code(ei) in ("YAML_TOO_DEEP", "YAML_UNSAFE")


def test_alias_expansion_bomb_is_rejected_quickly(tmp_path):
    """'Billion laughs': a <1 KB document whose aliases expand to 10^9
    leaves. It must be rejected promptly, not traversed/serialized."""
    lines = ['a: &a ["x","x","x","x","x","x","x","x","x","x"]']
    prev = "a"
    for c in "bcdefghi":
        lines.append(f"{c}: &{c} [" + ",".join(["*" + prev] * 10) + "]")
        prev = c
    bomb = "\n".join(lines) + "\n"
    assert len(bomb) < 1024
    script = (
        "import sys\n"
        "from lib import storage\n"
        "from lib.errors import ResumeTailorError\n"
        "try:\n"
        "    storage.yaml_load_text(sys.stdin.read())\n"
        "    print('ACCEPTED')\n"
        "except ResumeTailorError as e:\n"
        "    print(e.code)\n"
    )
    try:
        out = subprocess.run([PY, "-c", script], input=bomb, cwd=REPO_ROOT, capture_output=True,
                             text=True, timeout=5)
    except subprocess.TimeoutExpired:
        pytest.fail("alias-expansion bomb was not rejected within 5s (loader expands aliases unbounded)")
    assert out.stdout.strip() in ("YAML_UNSAFE", "YAML_TOO_LARGE", "YAML_TOO_DEEP"), out.stdout + out.stderr


# --------------------------------------------------------------------------
# Errors do not echo input
# --------------------------------------------------------------------------

def test_error_details_do_not_echo_input(tmp_path):
    secret = "SECRET-PII-9f8e7d"
    cases = [
        (f"name: {secret}\nx: !!python/object/apply:os.system ['{secret}']\n", "YAML_UNSAFE"),
        (f"{secret}: [unclosed {secret}\n", "YAML_UNSAFE"),
        (f"a: '{secret}" + "x" * storage.MAX_YAML_BYTES + "'\n", "YAML_TOO_LARGE"),
        (storage.dump_yaml({secret: _nested(25)}), "YAML_TOO_DEEP"),
    ]
    for text, code in cases:
        with pytest.raises(ResumeTailorError) as ei:
            storage.yaml_load_text(text)
        assert _code(ei) == code
        assert secret not in json.dumps(ei.value.details, default=str)
        assert secret not in ei.value.message
        assert secret not in json.dumps(ei.value.to_result(), default=str)
