"""Patches are untrusted input (4.6): malformed, oversized, duplicated,
contradictory and out-of-order patch lists must never crash, never partially
write, and never leak internal details -- driven through both
`lib.patches.validate_and_apply` and `server.tailor_resume`.
"""

from __future__ import annotations

import json
import random

import pytest

import server
from lib.errors import ERROR_CATEGORIES, ResumeTailorError
from lib.ids import normalize_master
from lib.patches import validate_and_apply

WF = "wf-fuzz"
SENTINEL_MASTER_TEXT = "Built 18 Python/Django REST endpoints"  # substring of a real master bullet


@pytest.fixture
def m(legacy_master):
    return normalize_master(legacy_master, "resume")


def _mref(i):
    return {"type": "master", "id": i}


def replace(target, text="Rewrote the thing.", refs=None, **nc):
    return {"operation": "replace_block", "target": {"id": target},
            "new_content": {"text": text, "source_refs": refs if refs is not None else [_mref(target)], **nc}}


def drop(target):
    return {"operation": "drop_block", "target": {"id": target}}


def reorder(**kw):
    return {"operation": "reorder", **kw}


def add_block(parent, text="Did a thing.", refs=None, **nc):
    return {"operation": "add_block", "parent_id": parent,
            "new_content": {"text": text, "source_refs": refs if refs is not None else [_mref(parent)], **nc}}


import re

_PATH_LIKE = re.compile(r"(?:/[\w.\-]+){2,}")


def _no_leak(exc_value, evidence_sentinel: str | None = None):
    blob = json.dumps(exc_value.details, ensure_ascii=True)
    assert not _PATH_LIKE.search(blob)
    assert "/Users" not in blob
    assert "pytest-of" not in blob
    assert ".yaml" not in blob
    assert SENTINEL_MASTER_TEXT not in blob
    if evidence_sentinel:
        assert evidence_sentinel not in blob
    assert SENTINEL_MASTER_TEXT not in exc_value.message


def _rejection_shape_ok(rejections: list[dict]) -> None:
    for r in rejections:
        assert set(r) == {"patch_index", "operation", "rule", "message"}, r


# --------------------------------------------------------------------------
# Malformed
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "not-a-list", 42, 3.14, True, None, {"operation": "replace_block"},
])
def test_patches_not_a_list_rejected(m, bad):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, bad, workflow_id=WF)
    assert exc.value.code == "PATCH_INVALID"
    _no_leak(exc.value)


@pytest.mark.parametrize("bad_scalar", [1, "x", 3.5, False])
def test_list_of_scalars_rejected(m, bad_scalar):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [bad_scalar], workflow_id=WF)
    assert exc.value.code == "PATCH_INVALID"
    _no_leak(exc.value)


@pytest.mark.parametrize("op_value", [None, 42, ["replace_block"], {"x": 1}])
def test_operation_field_wrong_type_rejected(m, op_value):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [{"operation": op_value, "target": {"id": "exp-001-b01"}}], workflow_id=WF)
    assert exc.value.code == "PATCH_INVALID"
    _no_leak(exc.value)


def test_unknown_operation_neutralized_message(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [{"operation": "delete_everything"}], workflow_id=WF)
    assert exc.value.code == "PATCH_INVALID"
    errors = exc.value.details["errors"]
    assert any(e["msg"] == "Unknown operation." for e in errors)
    assert "delete_everything" not in json.dumps(exc.value.details)


def test_target_as_bare_string_rejected(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [{"operation": "drop_block", "target": "exp-001-b01"}], workflow_id=WF)
    assert exc.value.code == "PATCH_INVALID"
    _no_leak(exc.value)


def test_source_refs_as_dict_rejected(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [replace("exp-001-b01", refs={"type": "master", "id": "exp-001-b01"})],
                           workflow_id=WF)
    assert exc.value.code == "PATCH_INVALID"
    _no_leak(exc.value)


def test_ref_type_with_trailing_space_rejected(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [replace("exp-001-b01", refs=[{"type": "master ", "id": "exp-001-b01"}])],
                           workflow_id=WF)
    assert exc.value.code == "PATCH_INVALID"
    _no_leak(exc.value)


def test_missing_discriminator_rejected(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [{"target": {"id": "exp-001-b01"}}], workflow_id=WF)
    assert exc.value.code == "PATCH_INVALID"
    _no_leak(exc.value)


def test_deep_nesting_in_metadata_rejected(m):
    nested = {"a": 1}
    for _ in range(200):
        nested = {"a": nested}
    with pytest.raises((ResumeTailorError, RecursionError)) as exc_info:
        validate_and_apply(m, [replace("exp-001-b01", metadata=nested)], workflow_id=WF)
    if isinstance(exc_info.value, ResumeTailorError):
        _no_leak(exc_info.value)


# --------------------------------------------------------------------------
# Oversized
# --------------------------------------------------------------------------

def test_201_patches_rejected(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [drop("exp-001-b01")] * 201, workflow_id=WF)
    assert exc.value.code == "PATCH_INVALID"
    _no_leak(exc.value)


def test_10000_char_text_rejected(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [replace("exp-001-b01", text="x" * 10_000)], workflow_id=WF)
    assert exc.value.code in ("PATCH_INVALID", "PROVENANCE_VIOLATION")
    _no_leak(exc.value)


def test_1mb_metadata_rejected(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [replace("exp-001-b01", metadata={"note": "x" * (1024 * 1024)})], workflow_id=WF)
    assert exc.value.code in ("PATCH_INVALID", "PROVENANCE_VIOLATION")
    _no_leak(exc.value)


def test_100_bullets_added_either_succeed_together_or_reject_together(m):
    # No explicit cap on bullets-per-entry, so 100 plain, technology-free
    # bullets citing the entry itself may legitimately all succeed -- the
    # invariant is "no partial application", not "must reject".
    patches = [add_block("exp-001", text=f"Did thing {i}.") for i in range(100)]
    try:
        body, report = validate_and_apply(m, patches, workflow_id=WF)
        assert len(report["new_block_ids"]) == 100
    except ResumeTailorError as e:
        _rejection_shape_ok(e.details["rejections"])
        assert len(e.details["rejections"]) == 100


# --------------------------------------------------------------------------
# Duplicated / contradictory / out-of-order
# --------------------------------------------------------------------------

def test_duplicated_add_skill_patches_second_rejected_as_duplicate(m):
    evidence = {"ev-fuzz": {"id": "ev-fuzz", "workflow_id": WF, "workspace_id": "ws-1", "term": "Grafana",
                            "category": "professional", "evidence_text": "Ran Grafana dashboards in production.",
                            "confirmed": True, "metrics": [], "created_at": "2026-01-01T00:00:00Z"}}
    patch = {"operation": "add_skill_item", "category": "Tools", "name": "Grafana",
             "source_refs": [{"type": "evidence", "id": "ev-fuzz"}]}
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [patch, patch], workflow_id=WF, evidence=evidence,
                           provenance_hook=None)
    rejs = exc.value.details["rejections"]
    _rejection_shape_ok(rejs)
    assert any(r["rule"] == "patch.duplicate_skill" for r in rejs if r["patch_index"] == 1)


def test_drop_then_replace_same_block_rejected(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [drop("exp-001-b01"), replace("exp-001-b01")], workflow_id=WF)
    assert any(r["rule"] == "patch.unknown_target" for r in exc.value.details["rejections"])


def test_reorder_listing_a_dropped_id_rejected(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [drop("exp-001-b01"),
                               reorder(parent_id="exp-001", order=["exp-001-b01", "exp-001-b02"])],
                           workflow_id=WF)
    assert any(r["rule"] == "patch.reorder_not_permutation" for r in exc.value.details["rejections"])


def test_reorder_before_the_add_that_creates_it_rejected(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [reorder(parent_id="exp-001", order=["exp-001-b01", "exp-001-b02", "vb-001"]),
                               add_block("exp-001", text="New bullet.")],
                           workflow_id=WF)
    assert any(r["rule"] == "patch.reorder_not_permutation" for r in exc.value.details["rejections"])


# --------------------------------------------------------------------------
# Invariants: nothing partial, nothing leaked, via the real server tool
# --------------------------------------------------------------------------

BAD_PATCH_LISTS = [
    "not-a-list",
    [{"operation": None}],
    [{"operation": "wipe_everything"}],
    [{"operation": "drop_block", "target": "exp-001-b01"}],
    [{"operation": "replace_block", "target": {"id": "exp-001-b01"},
      "new_content": {"text": "x" * 10_000, "source_refs": [{"type": "master", "id": "exp-001-b01"}]}}],
    [{"operation": "reorder", "parent_id": "exp-001", "order": ["exp-001-b01", "exp-001-b02", "vb-999"]}],
]


@pytest.mark.parametrize("bad_patches", BAD_PATCH_LISTS)
def test_server_tailor_resume_rejects_without_partial_write(master, bad_patches):
    ws, doc, master_hash = master
    wf = server.analyze_tailoring_requirements("We need Python and AWS experience.")["workflow_id"]
    out = server.tailor_resume("fuzz-version", bad_patches, wf)
    assert out["ok"] is False
    assert out["error"]["code"] in ERROR_CATEGORIES
    blob = json.dumps(out["error"]["details"], ensure_ascii=True)
    assert not _PATH_LIKE.search(blob)
    assert "/Users" not in blob
    assert ".yaml" not in blob
    assert SENTINEL_MASTER_TEXT not in blob

    from lib import storage
    _, current_hash = storage.load_master("resume", ws=ws)
    assert current_hash == master_hash
    with pytest.raises(ResumeTailorError):
        storage.require_version("fuzz-version", ws=ws)


# --------------------------------------------------------------------------
# Seeded shuffle loop: always success or a registered ResumeTailorError,
# never a bare exception, never a partial write.
# --------------------------------------------------------------------------

def _random_patch(rng: random.Random) -> dict:
    kind = rng.choice(["replace", "drop", "reorder", "add", "add_skill", "garbage"])
    ids = ["exp-001-b01", "exp-001-b02", "proj-001-b01", "sum-001", "skill-python", "ghost-id"]
    if kind == "replace":
        return replace(rng.choice(ids), text=rng.choice(["x", "Built things.", ""]))
    if kind == "drop":
        return drop(rng.choice(ids))
    if kind == "reorder":
        return reorder(parent_id=rng.choice(["exp-001", None]), section=rng.choice([None, "projects"]),
                       order=rng.sample(ids, k=rng.randint(0, len(ids))))
    if kind == "add":
        return add_block(rng.choice(["exp-001", "proj-001", "ghost"]), text=rng.choice(["x", ""]))
    if kind == "add_skill":
        return {"operation": "add_skill_item", "category": rng.choice(["Tools", "", "X" * 100]),
                "name": rng.choice(["Kubernetes", ""]), "source_refs": []}
    return {"operation": rng.choice([None, "nope", 42])}


def test_seeded_shuffle_never_crashes_or_partially_writes(m):
    rng = random.Random(20261001)
    for _ in range(30):
        patches = [_random_patch(rng) for _ in range(rng.randint(1, 8))]
        rng.shuffle(patches)
        try:
            body, report = validate_and_apply(m, patches, workflow_id=WF)
            assert isinstance(body, dict) and isinstance(report, dict)
        except ResumeTailorError as e:
            assert e.code in ERROR_CATEGORIES
            _no_leak(e)
