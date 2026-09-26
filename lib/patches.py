"""Structured patch validation and application (spec §13: source-of-truth enforcement).

Why this module exists
----------------------
In v1 Claude could hand back a complete replacement resume, and nothing
stopped it from inventing a metric, a title or a technology. In v2 the rule is
**Claude proposes, Python decides**:

* Claude may only send a list of small, typed patches (`lib.schemas.Patch`)
  addressed to the stable block IDs of the workspace master.
* Those patches are *untrusted input*. This module decides, deterministically,
  whether each one is allowed, and applies them only if **all** of them are.
* Every piece of new wording must cite where it came from (`source_refs`): a
  citable block of the ORIGINAL master or a confirmed evidence record from the
  same workflow. Refs are never inferred -- a patch without refs is rejected.
* There is no operation that can touch header fields (titles, companies,
  dates, degrees, name, contact, project name/stack/dates/github), and the
  patch models use `extra="forbid"`, so an attempt to smuggle one in fails
  at parse time.

Rejections carry rule IDs and block IDs only; they never quote patch text,
because that text may contain personal data and error details are returned
to the MCP client and written to audit logs.

Phase 3 plugs the claim-strength, placement, metric and technology rules in
through `provenance_hook` (see `validate_and_apply` for the `ctx` contract).
"""

from __future__ import annotations

import copy
import re
from typing import Any, Callable

from pydantic import BaseModel, TypeAdapter, ValidationError

from lib.errors import ResumeTailorError
from lib.ids import experience_category, index_blocks, project_category
from lib.schemas import (
    CLAIM_RANK,
    MASTER_SKILL_CATEGORY,
    REPAIR_SAFE_OPERATIONS,
    SUMMARY_ID,
    AddBlock,
    AddSkillItem,
    DropBlock,
    Patch,
    ReplaceBlock,
    Reorder,
    claim_rank,
)

MAX_PATCHES = 200
MAX_TEXT_LEN = 600
MAX_SKILL_NAME_LEN = 60
MAX_SKILL_CATEGORY_LEN = 60

CONTENT_KEYS = ("name", "contact", "summary", "skills", "experience", "education", "projects", "certifications")
REORDER_SECTIONS = ("experience", "projects", "education", "skills", "certifications")

# Entry sections -> (bullet block type, master category function)
_ENTRY_SECTIONS: dict[str, tuple[str, Callable[[dict], str]]] = {
    "experience": ("experience_bullet", experience_category),
    "projects": ("project_bullet", project_category),
    "education": ("education_bullet", lambda _e: "academic"),
}
_ENTRY_TYPES = {"experience": "experience", "projects": "project", "education": "education"}
_REPLACEABLE_TYPES = ("summary", "experience_bullet", "project_bullet", "education_bullet")

_PATCH_LIST_ADAPTER = TypeAdapter(list[Patch])
_PATCH_CLASSES = (ReplaceBlock, DropBlock, Reorder, AddBlock, AddSkillItem)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def _safe_loc_part(part: Any) -> Any:
    # Loc parts are field names / list indices; an extra key name is
    # attacker-controlled, so cap its length.
    return part if isinstance(part, int) else str(part)[:64]


def _safe_msg(err: dict) -> str:
    # pydantic's discriminator message echoes the supplied tag value.
    if err.get("type") == "union_tag_invalid":
        return "Unknown operation."
    return err.get("msg", "invalid")


def parse_patches(raw: list[dict]) -> list[Patch]:
    """Validate raw patch dicts into typed patch models.

    Raises PATCH_INVALID with `details={"errors": [{loc, msg}]}`. Input values
    are never echoed back.
    """
    if not isinstance(raw, list):
        raise ResumeTailorError("PATCH_INVALID", "patches must be a list",
                                details={"errors": [{"loc": [], "msg": "Input should be a list."}]})
    if len(raw) > MAX_PATCHES:
        raise ResumeTailorError("PATCH_INVALID", f"too many patches (max {MAX_PATCHES})",
                                details={"errors": [{"loc": [], "msg": f"At most {MAX_PATCHES} patches allowed."}]})
    try:
        return _PATCH_LIST_ADAPTER.validate_python(raw)
    except ValidationError as exc:
        errors = [{"loc": [_safe_loc_part(p) for p in e.get("loc", ())], "msg": _safe_msg(e)}
                  for e in exc.errors(include_input=False, include_url=False)]
        raise ResumeTailorError("PATCH_INVALID", "patches failed schema validation",
                                details={"errors": errors}) from None


def _coerce_patches(patches) -> list[Patch]:
    if isinstance(patches, list) and all(isinstance(p, _PATCH_CLASSES) for p in patches):
        if len(patches) > MAX_PATCHES:
            return parse_patches(patches)  # raises the standard too-many error
        return list(patches)
    if isinstance(patches, list):
        patches = [p.model_dump() if isinstance(p, BaseModel) else p for p in patches]
    return parse_patches(patches)


# --------------------------------------------------------------------------
# Initial body
# --------------------------------------------------------------------------

def _self_ref(block_id: str) -> list[dict]:
    return [{"type": "master", "id": block_id}]


def initial_body(master: dict) -> dict:
    """Deep copy of the master's content with each citable leaf citing itself.

    This is copying, not inference: an untouched master bullet is supported by
    exactly that master bullet.
    """
    body = {key: copy.deepcopy(master.get(key)) for key in CONTENT_KEYS}
    body["name"] = body["name"] or ""
    body["contact"] = body["contact"] or {}
    body["summary"] = body["summary"] or ""
    for key in CONTENT_KEYS[3:]:
        body[key] = body[key] or []

    for section, (_type, category_of) in _ENTRY_SECTIONS.items():
        for entry in body[section]:
            category = category_of(entry)
            for bullet in entry.get("bullets") or []:
                bullet["source_refs"] = _self_ref(bullet["id"])
                bullet["claim_strength"] = category
    for group in body["skills"]:
        for item in group.get("items") or []:
            item["source_refs"] = _self_ref(item["id"])
            item["claim_strength"] = None
    for cert in body["certifications"]:
        cert["source_refs"] = _self_ref(cert["id"])
        cert["claim_strength"] = "certification"
    return body


# --------------------------------------------------------------------------
# Locating blocks in the *current* body
# --------------------------------------------------------------------------

def _locate(body: dict, block_id: str, summary_present: bool) -> dict | None:
    """Find a block in the current body.

    Returns `{type, section, container, index, parent}` where `container` is
    the list holding the block (None for the summary) and `parent` is the
    owning entry / skill group for bullets and skill items.
    """
    if block_id == SUMMARY_ID:
        return {"type": "summary", "section": "summary", "container": None, "index": None,
                "parent": None} if summary_present else None
    for section in _ENTRY_SECTIONS:
        entries = body[section]
        for i, entry in enumerate(entries):
            if entry.get("id") == block_id:
                return {"type": _ENTRY_TYPES[section],
                        "section": section, "container": entries, "index": i, "parent": None}
            bullets = entry.get("bullets") or []
            for j, bullet in enumerate(bullets):
                if bullet.get("id") == block_id:
                    return {"type": _ENTRY_SECTIONS[section][0], "section": section,
                            "container": bullets, "index": j, "parent": entry}
    groups = body["skills"]
    for i, group in enumerate(groups):
        if group.get("id") == block_id:
            return {"type": "skill_group", "section": "skills", "container": groups, "index": i, "parent": None}
        items = group.get("items") or []
        for j, item in enumerate(items):
            if item.get("id") == block_id:
                return {"type": "skill_item", "section": "skills", "container": items, "index": j,
                        "parent": group}
    certs = body["certifications"]
    for i, cert in enumerate(certs):
        if cert.get("id") == block_id:
            return {"type": "certification", "section": "certifications", "container": certs,
                    "index": i, "parent": None}
    return None


def _all_body_ids(body: dict) -> set[str]:
    ids: set[str] = set()
    for section in _ENTRY_SECTIONS:
        for entry in body[section]:
            ids.add(entry.get("id"))
            ids.update(b.get("id") for b in entry.get("bullets") or [])
    for group in body["skills"]:
        ids.add(group.get("id"))
        ids.update(i.get("id") for i in group.get("items") or [])
    ids.update(c.get("id") for c in body["certifications"])
    ids.discard(None)
    return ids


def _next_id(body: dict, prefix: str) -> str:
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    highest = max((int(m.group(1)) for i in _all_body_ids(body) if (m := pattern.match(i))), default=0)
    return f"{prefix}{highest + 1:03d}"


def _norm(s: str) -> str:
    return s.strip().casefold()


def _find_group(body: dict, category: str) -> dict | None:
    wanted = _norm(category)
    return next((g for g in body["skills"] if _norm(str(g.get("category", ""))) == wanted), None)


# --------------------------------------------------------------------------
# Source-ref validation
# --------------------------------------------------------------------------

def _enum_value(v: Any) -> Any:
    return getattr(v, "value", v)


def _short(s: Any) -> str:
    return str(s)[:80]


class _Ctx:
    """Everything the checks of one validate_and_apply call share."""

    def __init__(self, master: dict, workflow_id: str, evidence: dict[str, dict] | None):
        self.master_index = index_blocks(master)  # ORIGINAL master, never the body
        self.workflow_id = workflow_id
        self.evidence = evidence or {}


def _check_refs(refs: list[dict], ctx: _Ctx) -> tuple[list[dict], list[dict]]:
    """Validate source refs. Returns (rejections, ref_infos)."""
    if not refs:
        return [_rej("provenance.missing_refs", "New content must cite at least one source ref.")], []
    rejections: list[dict] = []
    infos: list[dict] = []
    for ref in refs:
        rid, rtype = ref["id"], ref["type"]
        if rtype == "master":
            block = ctx.master_index.get(rid)
            if block is None:
                rejections.append(_rej("provenance.unknown_master_ref",
                                       f"Master ref {_short(rid)} does not exist in the master."))
            elif block["category"] is None:
                rejections.append(_rej("provenance.uncitable_ref",
                                       f"Master block {_short(rid)} ({block['type']}) cannot be cited as evidence."))
            else:
                infos.append({"type": "master", "id": rid, "category": block["category"],
                              "text": block["text"], "section": block["section"]})
            continue
        record = ctx.evidence.get(rid)
        if record is None:
            rejections.append(_rej("provenance.unknown_evidence_ref", f"Evidence {_short(rid)} does not exist."))
            continue
        before = len(rejections)
        if record.get("workflow_id") != ctx.workflow_id:
            rejections.append(_rej("provenance.evidence_wrong_workflow",
                                   f"Evidence {_short(rid)} belongs to a different workflow."))
        if record.get("confirmed") is not True:
            rejections.append(_rej("provenance.evidence_unconfirmed",
                                   f"Evidence {_short(rid)} has not been confirmed by the candidate."))
        category = _enum_value(record.get("category"))
        if category not in CLAIM_RANK:
            rejections.append(_rej("provenance.evidence_invalid_category",
                                   f"Evidence {_short(rid)} has no valid category."))
        elif category == "none":
            rejections.append(_rej("provenance.evidence_none",
                                   f"Evidence {_short(rid)} has category 'none' and supports no claim."))
        if len(rejections) == before:
            infos.append({"type": "evidence", "id": rid, "category": category,
                          "text": record.get("evidence_text", ""), "section": None,
                          "term": record.get("term"), "metrics": list(record.get("metrics") or [])})
    return rejections, infos


def derive_claim_strength(ref_infos: list[dict]) -> str | None:
    """Strongest cited category by `claim_rank`.

    A master skill item is not a claim category of its own: if the strongest
    ref is a master skill (and no real category ties with it), the result is
    None. Among equal ranks a real category wins over master_skill; other ties
    keep the first-cited category, so the result is deterministic.
    """
    if not ref_infos:
        return None
    best = max(ref_infos, key=lambda r: (claim_rank(r["category"]), r["category"] != MASTER_SKILL_CATEGORY))
    return None if best["category"] == MASTER_SKILL_CATEGORY else best["category"]


# --------------------------------------------------------------------------
# Per-operation checks. Each returns (rejections, plan); `plan` carries what
# the matching _apply_* needs and is only used when there are no rejections.
# --------------------------------------------------------------------------

def _rej(rule: str, message: str) -> dict:
    return {"rule": rule, "message": message}


def _check_text(text: str) -> list[dict]:
    if not text.strip() or len(text) > MAX_TEXT_LEN:
        return [_rej("patch.text_length", f"Text must be non-empty and at most {MAX_TEXT_LEN} characters.")]
    return []


def _check_replace(p: ReplaceBlock, body: dict, state: dict, ctx: _Ctx):
    loc = _locate(body, p.target.id, state["summary_present"])
    if loc is None:
        return [_rej("patch.unknown_target", f"Target {_short(p.target.id)} does not exist.")], None
    if loc["type"] not in _REPLACEABLE_TYPES:
        return [_rej("patch.replace_target_type",
                     f"Target {_short(p.target.id)} is a {loc['type']}; only the summary and bullets can be replaced.")], None
    rejections = _check_text(p.new_content.text)
    ref_rej, infos = _check_refs([r.model_dump() for r in p.new_content.source_refs], ctx)
    rejections += ref_rej
    return rejections, {"loc": loc, "infos": infos, "target_type": loc["type"], "target_section": loc["section"],
                        "text": p.new_content.text}


def _check_drop(p: DropBlock, body: dict, state: dict, ctx: _Ctx):
    loc = _locate(body, p.target.id, state["summary_present"])
    if loc is None:
        return [_rej("patch.unknown_target", f"Target {_short(p.target.id)} does not exist.")], None
    return [], {"loc": loc}


def _reorder_scope(p: Reorder, body: dict, state: dict):
    """Resolve the list being reordered. Returns (rejections, list)."""
    if (p.parent_id is None) == (p.section is None):
        return [_rej("patch.reorder_scope", "Exactly one of parent_id or section must be given.")], None
    if p.section is not None:
        if p.section not in REORDER_SECTIONS:
            return [_rej("patch.reorder_scope", f"Section must be one of {', '.join(REORDER_SECTIONS)}.")], None
        return [], body[p.section]
    loc = _locate(body, p.parent_id, state["summary_present"])
    if loc is None:
        return [_rej("patch.unknown_target", f"Parent {_short(p.parent_id)} does not exist.")], None
    entry = loc["container"][loc["index"]] if loc["container"] is not None else None
    if loc["type"] in ("experience", "project", "education"):
        entry.setdefault("bullets", [])
        return [], entry["bullets"]
    if loc["type"] == "skill_group":
        entry.setdefault("items", [])
        return [], entry["items"]
    return [_rej("patch.reorder_scope",
                 f"Parent {_short(p.parent_id)} is a {loc['type']}; only entries and skill groups have children.")], None


def _check_reorder(p: Reorder, body: dict, state: dict, ctx: _Ctx):
    rejections, children = _reorder_scope(p, body, state)
    if rejections:
        return rejections, None
    current = [c.get("id") for c in children]
    if len(p.order) != len(set(p.order)) or sorted(p.order) != sorted(current):
        return [_rej("patch.reorder_not_permutation",
                     "order must list every current ID in scope exactly once, with no extras.")], None
    return [], {"children": children}


def _check_add_block(p: AddBlock, body: dict, state: dict, ctx: _Ctx):
    loc = _locate(body, p.parent_id, state["summary_present"])
    if loc is None:
        return [_rej("patch.unknown_target", f"Parent {_short(p.parent_id)} does not exist.")], None
    if loc["type"] not in ("experience", "project", "education"):
        return [_rej("patch.add_parent_type",
                     f"Parent {_short(p.parent_id)} is a {loc['type']}; bullets can only be added to entries.")], None
    rejections = _check_text(p.new_content.text)
    ref_rej, infos = _check_refs([r.model_dump() for r in p.new_content.source_refs], ctx)
    rejections += ref_rej
    entry = loc["container"][loc["index"]]
    return rejections, {"entry": entry, "infos": infos, "target_type": _ENTRY_SECTIONS[loc["section"]][0],
                        "target_section": loc["section"], "text": p.new_content.text}


def _check_add_skill(p: AddSkillItem, body: dict, state: dict, ctx: _Ctx):
    rejections: list[dict] = []
    if not p.category.strip() or len(p.category) > MAX_SKILL_CATEGORY_LEN:
        rejections.append(_rej("patch.text_length",
                               f"Skill category must be non-empty and at most {MAX_SKILL_CATEGORY_LEN} characters."))
    if not p.name.strip() or len(p.name) > MAX_SKILL_NAME_LEN:
        rejections.append(_rej("patch.text_length",
                               f"Skill name must be non-empty and at most {MAX_SKILL_NAME_LEN} characters."))
    group = _find_group(body, p.category) if p.category.strip() else None
    if group is not None and any(_norm(str(i.get("name", ""))) == _norm(p.name) for i in group.get("items") or []):
        rejections.append(_rej("patch.duplicate_skill",
                               f"Skill group {_short(group.get('id'))} already contains this skill."))
    ref_rej, infos = _check_refs([r.model_dump() for r in p.source_refs], ctx)
    rejections += ref_rej
    category = group.get("category") if group is not None else p.category.strip()
    return rejections, {"group": group, "infos": infos, "target_type": "skill_item", "target_section": "skills",
                        "text": p.name, "skill_group_category": category}


_CHECKS = {
    "replace_block": _check_replace,
    "drop_block": _check_drop,
    "reorder": _check_reorder,
    "add_block": _check_add_block,
    "add_skill_item": _check_add_skill,
}


# --------------------------------------------------------------------------
# Application (only reached for a patch whose checks all passed)
# --------------------------------------------------------------------------

def _claim(explicit, infos) -> str | None:
    explicit = _enum_value(explicit)
    return explicit if explicit is not None else derive_claim_strength(infos)


def _apply_replace(p: ReplaceBlock, plan: dict, body: dict, state: dict, report: dict) -> str:
    nc = p.new_content
    refs = [r.model_dump() for r in nc.source_refs]
    strength = _claim(nc.claim_strength, plan["infos"])
    loc = plan["loc"]
    if loc["type"] == "summary":
        body["summary"] = nc.text
        state["summary_refs"], state["summary_claim"] = refs, strength
    else:
        loc["container"][loc["index"]] = {"id": p.target.id, "text": nc.text, "source_refs": refs,
                                          "claim_strength": strength, "metadata": copy.deepcopy(nc.metadata)}
    report["changed_block_ids"].append(p.target.id)
    return p.target.id


def _apply_drop(p: DropBlock, plan: dict, body: dict, state: dict, report: dict) -> str:
    loc = plan["loc"]
    if loc["type"] == "summary":
        body["summary"] = ""
        state["summary_present"] = False
        state["summary_refs"], state["summary_claim"] = [], None
    else:
        del loc["container"][loc["index"]]
    report["dropped_block_ids"].append(p.target.id)
    return p.target.id


def _apply_reorder(p: Reorder, plan: dict, body: dict, state: dict, report: dict) -> str:
    children = plan["children"]
    by_id = {c.get("id"): c for c in children}
    children[:] = [by_id[i] for i in p.order]
    return p.parent_id if p.parent_id is not None else p.section


def _apply_add_block(p: AddBlock, plan: dict, body: dict, state: dict, report: dict) -> str:
    nc = p.new_content
    new_id = _next_id(body, "vb-")
    plan["entry"].setdefault("bullets", []).append({
        "id": new_id, "text": nc.text, "source_refs": [r.model_dump() for r in nc.source_refs],
        "claim_strength": _claim(nc.claim_strength, plan["infos"]), "metadata": copy.deepcopy(nc.metadata)})
    report["new_block_ids"].append(new_id)
    return new_id


def _apply_add_skill(p: AddSkillItem, plan: dict, body: dict, state: dict, report: dict) -> str:
    group = plan["group"]
    if group is None:
        group = {"id": _next_id(body, "vg-"), "category": p.category.strip(), "items": []}
        body["skills"].append(group)
        report["new_block_ids"].append(group["id"])
    new_id = _next_id(body, "vs-")
    group.setdefault("items", []).append({
        "id": new_id, "name": p.name.strip(), "source_refs": [r.model_dump() for r in p.source_refs],
        "claim_strength": _claim(p.claim_strength, plan["infos"])})
    report["new_block_ids"].append(new_id)
    return new_id


_APPLY = {
    "replace_block": _apply_replace,
    "drop_block": _apply_drop,
    "reorder": _apply_reorder,
    "add_block": _apply_add_block,
    "add_skill_item": _apply_add_skill,
}

# Operations that introduce new wording and therefore must cite sources and
# go through the provenance hook.
_CONTENT_OPERATIONS = ("replace_block", "add_block", "add_skill_item")


def _hook_ctx(index: int, patch, plan: dict) -> dict:
    if patch.operation == "add_skill_item":
        refs, explicit = patch.source_refs, patch.claim_strength
    else:
        refs, explicit = patch.new_content.source_refs, patch.new_content.claim_strength
    return {
        "patch_index": index,
        "operation": patch.operation,
        "target_type": plan["target_type"],
        "target_section": plan["target_section"],
        "skill_group_category": plan.get("skill_group_category"),
        "text": plan["text"],
        "source_refs": [r.model_dump() for r in refs],
        "claim_strength": _claim(explicit, plan["infos"]),
        "ref_infos": copy.deepcopy(plan["infos"]),
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def validate_and_apply(master: dict, patches, *, workflow_id: str,
                       evidence: dict[str, dict] | None = None, repair_mode: bool = False,
                       provenance_hook: Callable[[dict], list[dict] | None] | None = None) -> tuple[dict, dict]:
    """Validate every patch against `master` and apply them all, or none.

    Patches apply in order, each against the body left by the previous ones.
    A rejected patch is not applied, and validation continues so the caller
    sees every rejection at once; if there is any, a single error is raised
    (PROVENANCE_VIOLATION if any rule starts with `provenance.`, else
    PATCH_INVALID) with `details={"rejections": [{patch_index, operation,
    rule, message}]}` and nothing is returned.

    `provenance_hook(ctx)` is called for a content patch (replace_block,
    add_block, add_skill_item) only after its structural and ref checks pass.
    It returns a list of `{"rule", "message"}` dicts (or None) that become
    rejections of that patch. `ctx` keys:

    - `patch_index`, `operation`
    - `target_type`: summary | experience_bullet | project_bullet |
      education_bullet | skill_item
    - `target_section`: summary | experience | projects | education | skills
    - `skill_group_category`: the (existing or new) group's category for
      add_skill_item, else None
    - `text`: the new text (the skill name for add_skill_item)
    - `source_refs`: the patch's refs as dicts `{type, id}`
    - `claim_strength`: the explicit value, or the one derived from the refs
    - `ref_infos`: per ref `{type, id, category, text, section}`; evidence
      refs also carry `term` and `metrics`, use `evidence_text` as `text`,
      and have `section` None. Master categories may be `master_skill`.

    Returns `(body, report)`; `master` is never mutated.
    """
    parsed = _coerce_patches(patches)
    ctx = _Ctx(master, workflow_id, evidence)
    body = initial_body(master)
    state = {"summary_present": True, "summary_refs": _self_ref(SUMMARY_ID), "summary_claim": None}
    report: dict[str, Any] = {"applied": [], "new_block_ids": [], "changed_block_ids": [], "dropped_block_ids": []}
    rejections: list[dict] = []

    for index, patch in enumerate(parsed):
        op = patch.operation

        def reject(items, op=op, index=index):
            rejections.extend({"patch_index": index, "operation": op, **r} for r in items)

        if repair_mode and op not in REPAIR_SAFE_OPERATIONS:
            reject([_rej("repair.forbidden_operation", f"Operation {op} is not allowed in repair mode.")])
            continue
        problems, plan = _CHECKS[op](patch, body, state, ctx)
        if not problems and provenance_hook is not None and op in _CONTENT_OPERATIONS:
            problems = [_rej(str(r.get("rule", "provenance.hook")), str(r.get("message", "")))
                        for r in (provenance_hook(_hook_ctx(index, patch, plan)) or [])]
        if problems:
            reject(problems)
            continue
        block_id = _APPLY[op](patch, plan, body, state, report)
        report["applied"].append({"patch_index": index, "operation": op, "block_id": block_id})

    if rejections:
        code = ("PROVENANCE_VIOLATION" if any(r["rule"].startswith("provenance.") for r in rejections)
                else "PATCH_INVALID")
        raise ResumeTailorError(code, f"{len(rejections)} patch rejection(s); no patches were applied.",
                                details={"rejections": rejections})

    remaining = _all_body_ids(body) | ({SUMMARY_ID} if state["summary_present"] else set())
    report["new_block_ids"] = [i for i in report["new_block_ids"] if i in remaining]
    report["changed_block_ids"] = list(dict.fromkeys(i for i in report["changed_block_ids"] if i in remaining))
    report["summary"] = {"source_refs": state["summary_refs"], "claim_strength": state["summary_claim"]}
    return body, report
