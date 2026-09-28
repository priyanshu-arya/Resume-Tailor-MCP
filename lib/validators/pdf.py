"""Critical PDF verification (spec §41/§42).

Every check here reads the compiled PDF with real libraries -- page count,
page size and embedded fonts via pypdf, characters/text via pdfplumber.
Nothing is inferred from the LaTeX source. If the libraries are missing the
release is blocked, and nothing is reported as measured.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import unicodedata
from collections import Counter
from pathlib import Path

from lib.schemas import Check

BLOCKED_MESSAGE = "PDF validation unavailable. Production release blocked."

SOURCE = "pdf_measured"
PT_PER_IN = 72.0
PAGE_SIZE_TOLERANCE_PT = 1.0
BODY_FLOOR_PT = 10.0
MARGIN_FLOOR_IN = 0.5
MARGIN_TOLERANCE_IN = 0.02
MIN_WORDS = 30
SMALL_CHAR_MAX_FRACTION = 0.02
COLUMN_SPLIT_FRACTION = 0.40

# Every check this module can emit, in report order.
ALL_CHECK_IDS = (
    "pdf.integrity",
    "pdf.page_count",
    "pdf.page_size",
    "pdf.fonts",
    "pdf.body_font_size",
    "pdf.margins",
    "pdf.text_extractable",
    "pdf.required_sections",
    "pdf.empty_pages",
    "pdf.content_present",
    "pdf.columns",
    "pdf.overfull_boxes",
)

_SUBSET_RE = re.compile(r"^[A-Z]{6}\+")


# --------------------------------------------------------------------------
# Backend detection
# --------------------------------------------------------------------------

def detect_pdf_backend() -> dict:
    backends = [name for name in ("pypdf", "pdfplumber") if importlib.util.find_spec(name) is not None]
    poppler = bool(shutil.which("pdfinfo") and shutil.which("pdftotext"))
    available = "pypdf" in backends and "pdfplumber" in backends
    if poppler:
        backends.append("poppler")
    return {
        "available": available,
        "backends": backends,
        "poppler": poppler,
        "message": "PDF validation available." if available else BLOCKED_MESSAGE,
    }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _check(id_, status, severity, category, message, measurement=None, expected=None, source=SOURCE) -> Check:
    return Check(id=id_, status=status, severity=severity, category=category,
                 measurement=measurement, expected=expected, source=source, message=message)


def _block(contract: dict, *path):
    """Return the contract value at `path`, or None when it (or any parent)
    is missing or declared "unknown" -- such checks are not_available."""
    node = contract
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
        if node == "unknown":
            return None
    return node


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("–", "-").replace("—", "-").replace("’", "'")
    return re.sub(r"\s+", " ", text).strip().lower()


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", _norm(text))


def _in_text(needle: str, norm_text: str, squashed_text: str) -> bool:
    n = _norm(needle)
    if not n:
        return True
    return n in norm_text or _squash(needle) in squashed_text


def _strip_subset(name: str) -> str:
    name = name.lstrip("/")
    return _SUBSET_RE.sub("", name)


def _embedded_fonts(reader) -> list[str]:
    """BaseFont names from every page's font resources (incl. Form XObjects)."""
    names: set[str] = set()

    def walk(resources, depth=0):
        if resources is None or depth > 5:
            return
        resources = resources.get_object()
        fonts = resources.get("/Font")
        if fonts is not None:
            for ref in fonts.get_object().values():
                font = ref.get_object()
                base = font.get("/BaseFont")
                if base is not None:
                    names.add(str(base).lstrip("/"))
        xobjects = resources.get("/XObject")
        if xobjects is not None:
            for ref in xobjects.get_object().values():
                xo = ref.get_object()
                if xo.get("/Subtype") == "/Form":
                    walk(xo.get("/Resources"), depth + 1)

    for page in reader.pages:
        walk(page.get("/Resources"))
    return sorted(names)


def _poppler_page_count(pdf_path: Path) -> int | None:
    try:
        out = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else None


def _lines(chars: list[dict], tol: float = 2.0) -> list[list[dict]]:
    """Group chars into visual lines by baseline proximity."""
    lines: list[list[dict]] = []
    for c in sorted(chars, key=lambda c: (round(c["bottom"], 1), c["x0"])):
        if lines and abs(lines[-1][0]["bottom"] - c["bottom"]) <= tol:
            lines[-1].append(c)
        else:
            lines.append([c])
    return lines


def _segments(line: list[dict], gap: float = 12.0) -> list[list[dict]]:
    """Split a line into runs separated by a horizontal gap wider than `gap` pt."""
    segs: list[list[dict]] = []
    for c in sorted(line, key=lambda c: c["x0"]):
        if segs and c["x0"] - segs[-1][-1]["x1"] > gap:
            segs.append([c])
        elif segs:
            segs[-1].append(c)
        else:
            segs.append([c])
    return segs


def _doc_sections_present(doc: dict) -> list[str]:
    return [key for key in ("summary", "experience", "projects", "skills", "education", "certifications") if doc.get(key)]


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------

def check_pdf(pdf_path, contract: dict, doc: dict, max_pages: int, compile_info: dict | None = None,
              backend: dict | None = None) -> tuple[list[Check], dict, list[str]]:
    """Measure the PDF against the template contract.

    Returns (checks, measured_properties, not_available). Only values read
    from the PDF go into measured_properties.
    """
    backend = backend if backend is not None else detect_pdf_backend()
    if not backend.get("available"):
        return (
            [_check("pdf.backend", "fail", "critical", "LATEX_PDF", BLOCKED_MESSAGE,
                    measurement={"backends": backend.get("backends", [])}, expected=["pypdf", "pdfplumber"])],
            {},
            list(ALL_CHECK_IDS),
        )

    import pdfplumber
    import pypdf

    pdf_path = Path(pdf_path)
    contract = contract or {}
    doc = doc or {}
    checks: list[Check] = []
    measured: dict = {}
    not_available: list[str] = []

    def na(check_id: str, why: str, category: str = "FORMAT"):
        not_available.append(check_id)
        checks.append(_check(check_id, "not_available", "info", category, why))

    # ---- integrity ------------------------------------------------------
    reader = None
    integrity_error = None
    try:
        reader = pypdf.PdfReader(str(pdf_path))
        if reader.is_encrypted:
            integrity_error = "PDF is encrypted."
        elif len(reader.pages) < 1:
            integrity_error = "PDF has no pages."
    except Exception as e:  # noqa: BLE001 - any parse failure means a broken PDF
        integrity_error = f"PDF could not be opened ({type(e).__name__})."
    if integrity_error:
        checks.append(_check("pdf.integrity", "fail", "critical", "LATEX_PDF", integrity_error))
        for cid in ALL_CHECK_IDS[1:]:
            not_available.append(cid)
        return checks, measured, not_available
    checks.append(_check("pdf.integrity", "pass", "critical", "LATEX_PDF", "PDF opens, is not encrypted and has pages."))

    # ---- page count -----------------------------------------------------
    page_count = len(reader.pages)
    measured["page_count"] = page_count
    limits = _block(contract, "limits")
    min_pages = limits.get("min_pages", 1) if isinstance(limits, dict) else 1
    pc_measure = {"pypdf": page_count}
    if backend.get("poppler"):
        pc_measure["poppler"] = _poppler_page_count(pdf_path)
    if page_count > max_pages:
        checks.append(_check("pdf.page_count", "fail", "critical", "FORMAT",
                             f"PDF has {page_count} pages; the limit is {max_pages}.",
                             pc_measure, {"min": min_pages, "max": max_pages}))
    elif page_count < min_pages:
        checks.append(_check("pdf.page_count", "fail", "error", "FORMAT",
                             f"PDF has {page_count} pages; at least {min_pages} expected.",
                             pc_measure, {"min": min_pages, "max": max_pages}))
    elif "poppler" in pc_measure and pc_measure["poppler"] not in (None, page_count):
        checks.append(_check("pdf.page_count", "fail", "error", "FORMAT",
                             "pypdf and poppler disagree on the page count.",
                             pc_measure, {"min": min_pages, "max": max_pages}))
    else:
        checks.append(_check("pdf.page_count", "pass", "critical", "FORMAT",
                             f"{page_count} page(s), within {min_pages}-{max_pages}.",
                             pc_measure, {"min": min_pages, "max": max_pages}))

    # ---- page size ------------------------------------------------------
    sizes = []
    for page in reader.pages:
        box = page.mediabox
        sizes.append([round(float(box.width), 2), round(float(box.height), 2)])
    measured["page_sizes_pt"] = sizes
    page_c = _block(contract, "page")
    if not isinstance(page_c, dict) or page_c.get("width_pt") is None or page_c.get("height_pt") is None:
        na("pdf.page_size", "Contract page size is unknown; page size not validated.")
    else:
        exp = [float(page_c["width_pt"]), float(page_c["height_pt"])]
        bad = [i + 1 for i, (w, h) in enumerate(sizes)
               if abs(w - exp[0]) > PAGE_SIZE_TOLERANCE_PT or abs(h - exp[1]) > PAGE_SIZE_TOLERANCE_PT]
        if bad:
            checks.append(_check("pdf.page_size", "fail", "critical", "FORMAT",
                                 f"Page(s) {bad} are not {exp[0]:g}x{exp[1]:g} pt (+/-1 pt).", sizes, exp))
        else:
            checks.append(_check("pdf.page_size", "pass", "critical", "FORMAT",
                                 f"All pages are {exp[0]:g}x{exp[1]:g} pt (+/-1 pt).", sizes, exp))

    # ---- fonts ----------------------------------------------------------
    fonts = _embedded_fonts(reader)
    measured["fonts"] = fonts
    prefixes = _block(contract, "typography", "pdf_font_prefixes")
    if not prefixes:
        na("pdf.fonts", "Contract font list is unknown; fonts not validated.")
    else:
        offending = [f for f in fonts if not any(_strip_subset(f).startswith(p) for p in prefixes)]
        if offending:
            checks.append(_check("pdf.fonts", "fail", "error", "FORMAT",
                                 f"Unexpected embedded font(s): {offending}.", fonts, prefixes))
        else:
            checks.append(_check("pdf.fonts", "pass", "error", "FORMAT",
                                 "All embedded fonts match the template's font family.", fonts, prefixes))

    # ---- text / chars via pdfplumber ------------------------------------
    page_texts: list[str] = []
    page_chars: list[list[dict]] = []
    page_dims: list[tuple[float, float]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            page_texts.append(page.extract_text() or "")
            page_chars.append([c for c in page.chars if (c.get("text") or "").strip()])
            page_dims.append((float(page.width), float(page.height)))
    full_text = "\n".join(page_texts)
    norm_text, squashed_text = _norm(full_text), _squash(full_text)
    word_count = len(full_text.split())
    measured["word_count"] = word_count

    # ---- body font size -------------------------------------------------
    body_c = _block(contract, "typography", "body")
    tolerance = _block(contract, "typography", "size_tolerance_pt")
    tolerance = float(tolerance) if tolerance is not None else 0.1
    alnum = [c for chars in page_chars for c in chars if c["text"].isalnum()]
    if alnum:
        sizes_counter = Counter(round(float(c["size"]), 2) for c in alnum)
        modal = sizes_counter.most_common(1)[0][0]
        measured["body_font_pt_modal"] = modal
        measured["min_char_pt"] = round(min(float(c["size"]) for c in alnum), 2)
    else:
        modal = None
    if not isinstance(body_c, dict) or body_c.get("size_pt") is None:
        na("pdf.body_font_size", "Contract body size is unknown; body font size not validated.")
    elif modal is None:
        checks.append(_check("pdf.body_font_size", "fail", "critical", "FORMAT",
                             "No text characters found to measure."))
    else:
        floor = BODY_FLOOR_PT - tolerance
        expected = float(body_c["size_pt"])
        # Bullets/symbols are non-alphanumeric and excluded; a small share of
        # tiny alphanumerics (superscripts, footnote marks) is tolerated.
        small = sum(1 for c in alnum if float(c["size"]) < floor)
        small_frac = small / len(alnum)
        measurement = {"modal_pt": modal, "chars_below_floor": small, "fraction_below_floor": round(small_frac, 4)}
        exp = {"size_pt": expected, "floor_pt": BODY_FLOOR_PT, "tolerance_pt": tolerance}
        if modal < floor:
            checks.append(_check("pdf.body_font_size", "fail", "critical", "FORMAT",
                                 f"Body text is {modal} pt, below the {BODY_FLOOR_PT:g} pt floor.", measurement, exp))
        elif abs(modal - expected) > tolerance:
            checks.append(_check("pdf.body_font_size", "fail", "error", "FORMAT",
                                 f"Body text is {modal} pt; the template specifies {expected:g} pt.", measurement, exp))
        elif small_frac > SMALL_CHAR_MAX_FRACTION:
            # Real text below the hard 10 pt floor is non-negotiable -> critical.
            checks.append(_check("pdf.body_font_size", "fail", "critical", "FORMAT",
                                 f"{small} text characters ({small_frac:.1%}) are below {BODY_FLOOR_PT:g} pt.",
                                 measurement, exp))
        else:
            checks.append(_check("pdf.body_font_size", "pass", "critical", "FORMAT",
                                 f"Body text is {modal} pt (template {expected:g} pt, floor {BODY_FLOOR_PT:g} pt).",
                                 measurement, exp))

    # ---- margins (ink extents) ------------------------------------------
    per_page = []
    for chars, (w, h) in zip(page_chars, page_dims):
        if not chars:
            continue
        per_page.append({
            "left": min(c["x0"] for c in chars) / PT_PER_IN,
            "right": (w - max(c["x1"] for c in chars)) / PT_PER_IN,
            "top": min(c["top"] for c in chars) / PT_PER_IN,
            "bottom": (h - max(c["bottom"] for c in chars)) / PT_PER_IN,
        })
    if per_page:
        margins = {side: round(min(p[side] for p in per_page), 3) for side in ("top", "bottom", "left", "right")}
        measured["margins_in"] = margins
        low = [side for side, v in margins.items() if v < MARGIN_FLOOR_IN - MARGIN_TOLERANCE_IN]
        note = "Measures text ink extents, which are >= the geometric page margin."
        if low:
            checks.append(_check("pdf.margins", "fail", "critical", "FORMAT",
                                 f"Margin(s) {low} below {MARGIN_FLOOR_IN} in. {note}", margins,
                                 {"min_in": MARGIN_FLOOR_IN}))
        else:
            checks.append(_check("pdf.margins", "pass", "critical", "FORMAT",
                                 f"All text is at least {MARGIN_FLOOR_IN} in from every edge. {note}", margins,
                                 {"min_in": MARGIN_FLOOR_IN}))
    else:
        checks.append(_check("pdf.margins", "fail", "critical", "FORMAT", "No text found to measure margins."))

    # ---- text extractable -----------------------------------------------
    if word_count >= MIN_WORDS:
        checks.append(_check("pdf.text_extractable", "pass", "critical", "LATEX_PDF",
                             f"{word_count} words extracted.", word_count, {"min_words": MIN_WORDS}))
    else:
        checks.append(_check("pdf.text_extractable", "fail", "critical", "LATEX_PDF",
                             f"Only {word_count} words could be extracted.", word_count, {"min_words": MIN_WORDS}))

    # ---- required section headings --------------------------------------
    headings = _block(contract, "sections", "headings")
    if not isinstance(headings, dict):
        na("pdf.required_sections", "Contract section headings are unknown; headings not validated.")
    else:
        wanted = {sec: headings[sec] for sec in _doc_sections_present(doc) if sec in headings}
        missing = [sec for sec, head in wanted.items() if not _in_text(head, norm_text, squashed_text)]
        if missing:
            checks.append(_check("pdf.required_sections", "fail", "error", "FORMAT",
                                 f"Section heading(s) missing from the PDF text: {missing}.",
                                 {"missing": missing}, sorted(wanted)))
        else:
            checks.append(_check("pdf.required_sections", "pass", "error", "FORMAT",
                                 "Every non-empty section's heading is present in the PDF text.",
                                 {"missing": []}, sorted(wanted)))

    # ---- empty pages ----------------------------------------------------
    empty = [i + 1 for i, t in enumerate(page_texts) if not t.strip()]
    if empty:
        checks.append(_check("pdf.empty_pages", "fail", "error", "FORMAT", f"Page(s) {empty} contain no text.", empty))
    else:
        checks.append(_check("pdf.empty_pages", "pass", "error", "FORMAT", "Every page contains text.", []))

    # ---- content present ------------------------------------------------
    blocks: list[tuple[str, str]] = []
    if doc.get("name"):
        blocks.append(("name", doc["name"]))
    for i, exp in enumerate(doc.get("experience") or []):
        if isinstance(exp, dict) and exp.get("company"):
            blocks.append((exp.get("id") or f"experience[{i}]", exp["company"]))
    for i, proj in enumerate(doc.get("projects") or []):
        if isinstance(proj, dict) and proj.get("name"):
            blocks.append((proj.get("id") or f"projects[{i}]", proj["name"]))
    missing_ids = [bid for bid, text in blocks if not _in_text(text, norm_text, squashed_text)]
    if missing_ids:
        checks.append(_check("pdf.content_present", "fail", "error", "LATEX_PDF",
                             f"Block(s) not found in the PDF text: {missing_ids}.",
                             {"missing_block_ids": missing_ids}, {"checked": len(blocks)}))
    else:
        checks.append(_check("pdf.content_present", "pass", "error", "LATEX_PDF",
                             f"Name, companies and project names ({len(blocks)} blocks) all appear in the PDF text.",
                             {"missing_block_ids": []}, {"checked": len(blocks)}))

    # ---- columns (heuristic) --------------------------------------------
    layout = _block(contract, "layout")
    if not isinstance(layout, dict) or layout.get("columns") is None:
        na("pdf.columns", "Contract layout is unknown; column count not validated.")
    else:
        worst = {"lines": 0, "right_column_lines": 0}
        multi = False
        for chars, (w, _h) in zip(page_chars, page_dims):
            lines = _lines(chars)
            split = w * COLUMN_SPLIT_FRACTION
            # A second column shows up as many lines whose right-hand run starts
            # at the SAME x (a column edge) while text also starts at the left.
            # Right-aligned dates/locations end at a common x but start anywhere.
            starts = Counter()
            for line in lines:
                segs = _segments(line)
                if len(segs) >= 2 and segs[0][0]["x0"] < split:
                    for seg in segs[1:]:
                        if seg[0]["x0"] >= split and len(seg) >= 3:
                            starts[round(seg[0]["x0"] / 4.0)] += 1
            top = starts.most_common(1)[0][1] if starts else 0
            if top > worst["right_column_lines"]:
                worst = {"lines": len(lines), "right_column_lines": top}
            if lines and top >= 8 and top >= 0.25 * len(lines):
                multi = True
        expected_cols = layout.get("columns")
        if multi and expected_cols == 1:
            checks.append(_check("pdf.columns", "warning", "warning", "FORMAT",
                                 "Heuristic: text appears to run in parallel columns; the template is single-column.",
                                 worst, {"columns": expected_cols}))
        else:
            checks.append(_check("pdf.columns", "pass", "warning", "FORMAT",
                                 "Heuristic: no parallel second text column detected.",
                                 worst, {"columns": expected_cols}))

    # ---- overfull boxes (from the compile log) ----------------------------
    if compile_info is None or compile_info.get("overfull_hbox_count") is None:
        na("pdf.overfull_boxes", "No compile log supplied; overfull boxes not checked.", "LATEX_PDF")
    else:
        n = int(compile_info["overfull_hbox_count"])
        if n > 0:
            checks.append(_check("pdf.overfull_boxes", "warning", "warning", "LATEX_PDF",
                                 f"{n} overfull \\hbox warning(s) in the final TeX pass.", n, 0,
                                 source="compile_log"))
        else:
            checks.append(_check("pdf.overfull_boxes", "pass", "warning", "LATEX_PDF",
                                 "No overfull \\hbox warnings.", 0, 0, source="compile_log"))

    return checks, measured, not_available
