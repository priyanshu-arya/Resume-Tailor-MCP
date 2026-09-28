"""Pre-compile format checks inferred from the generated LaTeX (spec §32, §40).

Pure and deterministic. Parses the .tex that lib/latex.render_latex emits
(or a tampered copy of it) and infers page size, class font size, margins,
body font size, column count, font family and forbidden layout structures,
then compares them with the template contract. These are `tex_inferred`
properties; the compiled PDF is measured separately (lib/validators/pdf.py).

Two kinds of check:
  * contract checks (TEMPLATE) -- `not_available` when the contract field is
    missing or "unknown", never `pass`;
  * formatting-floor checks (FORMAT) -- CLAUDE.md hard limits (margins
    >= 0.5 in, body >= 10 pt, single column, no graphics/tables/text boxes,
    no decorative fonts) that fail regardless of what the contract says.
"""

from __future__ import annotations

import re
from typing import Any

from lib.schemas import Check

SOURCE = "tex_inferred"
UNKNOWN = "unknown"

# Every check this module can emit, in report order.
ALL_CHECK_IDS = (
    "template.page_size",
    "template.base_font_size",
    "template.margins",
    "template.margin_floor",
    "template.body_font_size",
    "template.body_font_floor",
    "template.forbidden_structures",
    "template.columns",
    "template.font_family",
    "template.name_font_size",
    "template.heading_font_size",
    "template.heading_style",
    "template.spacing",
    "template.bullet_style",
    "template.link_style",
)

MARGIN_TOLERANCE_IN = 0.02
MIN_MARGIN_IN = 0.5
MIN_BODY_PT = 10.0
_EPS = 1e-6

PAPER_IN = {"letter": (8.5, 11.0), "a4": (210 / 25.4, 297 / 25.4)}
_PAPER_OPTIONS = {"letterpaper": "letter", "a4paper": "a4", "legalpaper": "legal",
                  "a5paper": "a5", "b5paper": "b5", "executivepaper": "executive"}
_CLASS_SIZES = ("10pt", "11pt", "12pt")

# Font-size switches of the standard classes (article/size1x.clo), in pt.
_SIZE_TABLE = {
    10: {"tiny": 5, "scriptsize": 7, "footnotesize": 8, "small": 9, "normalsize": 10, "large": 12,
         "Large": 14.4, "LARGE": 17.28, "huge": 20.74, "Huge": 24.88},
    11: {"tiny": 6, "scriptsize": 8, "footnotesize": 9, "small": 10, "normalsize": 10.95, "large": 12,
         "Large": 14.4, "LARGE": 17.28, "huge": 20.74, "Huge": 24.88},
    12: {"tiny": 6, "scriptsize": 8, "footnotesize": 10, "small": 10.95, "normalsize": 12, "large": 14.4,
         "Large": 17.28, "LARGE": 20.74, "huge": 24.88, "Huge": 24.88},
}
_SIZE_CMD_RE = re.compile(r"\\(tiny|scriptsize|footnotesize|small|normalsize|large|Large|LARGE|huge|Huge)(?![A-Za-z])")
_FONTSIZE_RE = re.compile(r"\\fontsize\s*\{\s*([\d.]+)\s*(pt|bp)?\s*\}")

_UNIT_IN = {"in": 1.0, "cm": 1 / 2.54, "mm": 1 / 25.4, "pt": 1 / 72.27, "bp": 1 / 72.0}
_LEN_RE = r"([-+]?\s*\d*\.?\d+)\s*(in|cm|mm|pt|bp)"
_LENGTH_CMD_RE = re.compile(
    r"\\(addtolength|setlength)\s*\{?\s*\\(topmargin|textheight|oddsidemargin|textwidth)\s*\}?\s*\{\s*"
    + _LEN_RE + r"\s*\}")

# Decorative / script font packages (page_and_format.fonts: "No script/decorative/novelty fonts").
DECORATIVE_FONT_PACKAGES = frozenset({
    "calligra", "frcursive", "yfonts", "aurical", "emerald", "suetterl", "fetamont", "cookingsymbols",
    "gothic", "oldgerm", "chancery", "pbsi", "bookhands", "romande", "tengwarscript", "calligraphy",
})
DECORATIVE_FONT_WORDS = ("script", "brush", "hand", "calligra", "comic", "papyrus", "chancery",
                         "cursive", "zapfino", "gothic", "fraktur", "blackletter")
# Plain families recognized from common packages; anything else loaded via a
# font package is reported by name and compared with the contract.
_FONT_PACKAGES = {
    "lmodern": "Latin Modern Roman", "helvet": "Helvetica", "mathptmx": "Times", "times": "Times",
    "newtxtext": "Times", "tgtermes": "Times", "tgheros": "Helvetica", "arev": "Arev Sans",
    "charter": "Charter", "XCharter": "Charter", "palatino": "Palatino", "mathpazo": "Palatino",
    "tgpagella": "Palatino", "sourcesanspro": "Source Sans Pro", "roboto": "Roboto", "carlito": "Calibri",
    "arial": "Arial", "uarial": "Arial", "libertine": "Linux Libertine", "fontin": "Fontin",
}
# Computer Modern and Latin Modern are the same design (LM is CM in Type 1/OTF).
_CM_EQUIVALENT = {"latin modern roman", "latin modern", "computer modern", "computer modern roman", "lmodern"}

# Environments/commands that put content in graphics, tables, columns or boxes.
_FORBIDDEN_ENVS = ("multicols", "multicols*", "minipage", "textblock", "textblock*", "tikzpicture",
                   "tabular", "tabular*", "tabularx", "longtable", "table", "figure", "wrapfigure", "picture")
_FORBIDDEN_CMDS = ("includegraphics", "parbox", "fbox", "framebox", "colorbox", "fcolorbox")

# Literal style-name -> required raw tokens, for `formatting.heading_style`.
# An unrecognized style string is `not_available`, never a guess at `pass`.
_HEADING_STYLE_TOKENS = {
    "bold small caps + rule": (r"\bfseries", r"\scshape", r"\titlerule", r"\vspace{2pt}"),
}
_CENTER_RE = re.compile(r"\\begin\{center\}(.*?)\\end\{center\}", re.S)
_TITLEFORMAT_SECTION_RE = re.compile(r"\\titleformat\{\\section\}([^\n]*)")
_LINESPREAD_RE = re.compile(r"\\linespread\s*\{\s*([\d.]+)\s*\}")
_BASELINESTRETCH_RE = re.compile(r"\\renewcommand\s*\{?\s*\\baselinestretch\s*\}?\s*\{")
_SPACING_CMDS_RE = re.compile(r"\\(onehalfspacing|doublespacing)\b")
_SETLIST_RE = re.compile(r"\\setlist(?:\[[^\]]*\])?\s*\{([^}]*)\}")


def _layout_macros(contract: dict) -> tuple[str, ...]:
    """The layout-only macro names a contract whitelists for the forbidden-
    structure scan (e.g. resumeSubheading's internal tabularx). Contract-
    driven, not hardcoded: an unknown/missing contract whitelists nothing, so
    a `.tex` defining `\\newcommand{\\resumeSubheading}` can't smuggle a
    tabularx body past the scan just by matching a name this module assumes."""
    names = _field(contract, "latex", "layout_only_macros")
    return tuple(names) if isinstance(names, list) else ()


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

def strip_comments(tex: str) -> str:
    """Drop % comments (an escaped \\% is kept)."""
    return "\n".join(re.sub(r"(?<!\\)%.*", "", line) for line in tex.splitlines())


def _brace_end(s: str, i: int) -> int:
    """Index just past the brace group starting at s[i] == '{' (or i if none)."""
    if i >= len(s) or s[i] != "{":
        return i
    depth = 0
    for k in range(i, len(s)):
        ch = s[k]
        if ch == "\\":
            continue
        if ch == "{" and (k == 0 or s[k - 1] != "\\"):
            depth += 1
        elif ch == "}" and s[k - 1] != "\\":
            depth -= 1
            if depth == 0:
                return k + 1
    return len(s)


def _remove_layout_macros(tex: str, macro_names: tuple[str, ...]) -> str:
    """Remove the definitions of the contract-whitelisted layout-only macros."""
    for name in macro_names:
        pattern = re.compile(r"\\(?:re)?newcommand\s*\{?\s*\\" + name + r"\s*\}?\s*(\[\d\])?\s*")
        m = pattern.search(tex)
        while m:
            end = _brace_end(tex, m.end())
            tex = tex[:m.start()] + tex[end:]
            m = pattern.search(tex)
    return tex


def _documentclass(tex: str) -> tuple[list[str], str | None]:
    m = re.search(r"\\documentclass\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}", tex)
    if not m:
        return [], None
    opts = [o.strip() for o in (m.group(1) or "").split(",") if o.strip()]
    return opts, m.group(2).strip()


def _packages(tex: str) -> list[tuple[list[str], str]]:
    out = []
    for m in re.finditer(r"\\usepackage\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}", tex):
        opts = [o.strip() for o in (m.group(1) or "").split(",") if o.strip()]
        for name in m.group(2).split(","):
            if name.strip():
                out.append((opts, name.strip()))
    return out


def _to_in(value: str, unit: str) -> float:
    return float(value.replace(" ", "")) * _UNIT_IN[unit]


def _geometry_margins(tex: str, packages) -> dict | None:
    opts: list[str] = []
    for p_opts, name in packages:
        if name == "geometry":
            opts += p_opts
    for m in re.finditer(r"\\geometry\s*\{([^}]*)\}", tex):
        opts += [o.strip() for o in m.group(1).split(",") if o.strip()]
    if not any(name == "geometry" for _, name in packages):
        return None
    sides = {"top": 1.0, "bottom": 1.0, "left": 1.0, "right": 1.0}  # approximation of geometry's defaults
    for opt in opts:
        if "=" not in opt:
            continue
        key, val = (x.strip() for x in opt.split("=", 1))
        m = re.fullmatch(_LEN_RE, val)
        if not m:
            continue
        v = _to_in(m.group(1), m.group(2))
        targets = {"margin": ("top", "bottom", "left", "right"), "hmargin": ("left", "right"), "vmargin": ("top", "bottom"),
                   "top": ("top",), "bottom": ("bottom",), "left": ("left",), "right": ("right",),
                   "inner": ("left",), "outer": ("right",)}.get(key)
        for side in (targets or ()):
            sides[side] = v
    return {k: round(v, 4) for k, v in sides.items()}


def infer_margins(tex: str, packages, paper: str | None) -> dict | None:
    """Margins in inches from fullpage (+ \\addtolength/\\setlength) or
    geometry. None when the margin mechanism is not one we can model."""
    wh = PAPER_IN.get(paper or "")
    if wh is None:
        return None
    geo = _geometry_margins(tex, packages)
    if geo is not None:
        return geo
    fullpage = [opts for opts, name in packages if name == "fullpage"]
    if not fullpage:
        return None
    base = 1.5 / 2.54 if "cm" in fullpage[0] else 1.0
    width, height = wh
    # fullpage: text starts `base` from every edge (header/footer zeroed by
    # the empty/plain styles). Lengths relative to that layout:
    lengths = {"topmargin": 0.0, "oddsidemargin": 0.0,
               "textheight": height - 2 * base, "textwidth": width - 2 * base}
    body = tex.split("\\begin{document}", 1)[0]
    for m in _LENGTH_CMD_RE.finditer(body):
        op, name, value, unit = m.groups()
        v = _to_in(value, unit)
        if op == "addtolength":
            lengths[name] += v
        else:  # \setlength of the LaTeX register: 1in + \topmargin / 1in + \oddsidemargin
            # \setlength sets the LaTeX register itself; for the offsets that is
            # 1in + register = margin, i.e. offset = register + 1in - base.
            lengths[name] = v if name in ("textheight", "textwidth") else v + 1.0 - base
    top = base + lengths["topmargin"]
    left = base + lengths["oddsidemargin"]
    return {"top": round(top, 4), "bottom": round(height - top - lengths["textheight"], 4),
            "left": round(left, 4), "right": round(width - left - lengths["textwidth"], 4)}


def _font_sizes(tex: str, base_pt: int) -> list[float]:
    table = _SIZE_TABLE[base_pt]
    sizes = [table[m.group(1)] for m in _SIZE_CMD_RE.finditer(tex)]
    sizes += [float(m.group(1)) * (72.27 / 72 if m.group(2) == "bp" else 1) for m in _FONTSIZE_RE.finditer(tex)]
    return sizes


def _font_family(tex: str, packages) -> tuple[str, list[str]]:
    decorative = [name for _, name in packages if name in DECORATIVE_FONT_PACKAGES]
    family = "Latin Modern Roman"  # class default (Computer/Latin Modern)
    for _, name in packages:
        if name in _FONT_PACKAGES:
            family = _FONT_PACKAGES[name]
    for m in re.finditer(r"\\set(?:main|sans)font\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", tex):
        family = m.group(1).strip()
        if any(w in family.lower() for w in DECORATIVE_FONT_WORDS):
            decorative.append(family)
    for m in re.finditer(r"\\(?:usefont\s*\{[^}]*\}\s*\{|fontfamily\s*\{)([^}]*)\}", tex):
        fam = m.group(1).strip()
        if any(w in fam.lower() for w in DECORATIVE_FONT_WORDS) or fam in DECORATIVE_FONT_PACKAGES:
            decorative.append(fam)
    return family, decorative


def _forbidden(tex: str, macro_names: tuple[str, ...]) -> list[str]:
    scan = _remove_layout_macros(tex, macro_names)
    found = []
    for env in _FORBIDDEN_ENVS:
        if re.search(r"\\begin\s*\{" + re.escape(env) + r"\}", scan):
            found.append(env)
    for cmd in _FORBIDDEN_CMDS:
        if re.search(r"\\" + cmd + r"(?![A-Za-z])", scan):
            found.append(cmd)
    return found


# --------------------------------------------------------------------------
# Contract access / checks
# --------------------------------------------------------------------------

def _field(contract: Any, *path: str):
    node = contract
    for key in path:
        if node is None or node == UNKNOWN or not isinstance(node, dict):
            return None
        node = node.get(key)
    return None if node is None or node == UNKNOWN else node


def _chk(id_, status, severity, category, measurement=None, expected=None, message="") -> Check:
    return Check(id=id_, status=status, severity=severity, category=category, measurement=measurement,
                 expected=expected, source=SOURCE, message=message)


def _na(id_, severity, category, measurement, why) -> Check:
    return _chk(id_, "not_available", severity, category, measurement, None, why)


def check_tex(tex: str, contract: dict) -> tuple[list[Check], dict]:
    """(checks, inferred_properties) for a LaTeX source vs a template contract."""
    contract = contract if isinstance(contract, dict) else {}
    src = strip_comments(tex or "")
    packages = _packages(src)
    opts, cls = _documentclass(src)
    checks: list[Check] = []

    # ---- page size
    papers = [_PAPER_OPTIONS[o] for o in opts if o in _PAPER_OPTIONS]
    paper = papers[-1] if papers else ("letter" if cls else None)  # standard-class default is letterpaper
    want_paper = _field(contract, "page", "size")
    if want_paper is None:
        checks.append(_na("template.page_size", "error", "TEMPLATE", paper, "Contract page size is unknown."))
    elif paper is None:
        checks.append(_na("template.page_size", "error", "TEMPLATE", None, "No \\documentclass found."))
    else:
        ok = paper == str(want_paper).lower()
        checks.append(_chk("template.page_size", "pass" if ok else "fail", "error", "TEMPLATE", paper, want_paper,
                           "" if ok else f"Page size is {paper}, contract requires {want_paper}."))

    # ---- class base font size
    size_opts = [o for o in opts if re.fullmatch(r"\d+(\.\d+)?pt", o)]
    requested = size_opts[-1] if size_opts else None
    base_pt = int(requested[:-2]) if requested in _CLASS_SIZES else 10  # unsupported sizes fall back to 10pt
    contract_opts = _field(contract, "latex", "documentclass_options")
    want_size = next((o for o in contract_opts if re.fullmatch(r"\d+pt", str(o))), None) \
        if isinstance(contract_opts, list) else None
    if want_size is None:
        checks.append(_na("template.base_font_size", "error", "TEMPLATE", requested or "10pt",
                          "Contract class font size is unknown."))
    else:
        actual = requested or "10pt"
        ok = actual == want_size and actual in _CLASS_SIZES
        msg = "" if ok else (f"Class size option {actual} is not supported by the standard classes."
                             if actual not in _CLASS_SIZES else f"Class size is {actual}, contract requires {want_size}.")
        checks.append(_chk("template.base_font_size", "pass" if ok else "fail", "error", "TEMPLATE",
                           actual, want_size, msg))

    # ---- margins
    margins = infer_margins(src, packages, paper)
    want_margins = _field(contract, "page", "margins_in")
    if margins is None:
        checks.append(_na("template.margins", "error", "TEMPLATE", None,
                          "Margins could not be inferred from the LaTeX (no fullpage/geometry or unknown paper)."))
    elif not isinstance(want_margins, dict) or any(_field(want_margins, s) is None
                                                   for s in ("top", "bottom", "left", "right")):
        checks.append(_na("template.margins", "error", "TEMPLATE", margins, "Contract margins are unknown."))
    else:
        off = [s for s in ("top", "bottom", "left", "right")
               if abs(margins[s] - float(want_margins[s])) > MARGIN_TOLERANCE_IN + _EPS]
        checks.append(_chk("template.margins", "fail" if off else "pass", "error", "TEMPLATE", margins,
                           {**{s: want_margins[s] for s in ("top", "bottom", "left", "right")},
                            "tolerance_in": MARGIN_TOLERANCE_IN},
                           f"Margin(s) differ from the contract: {', '.join(off)}." if off else ""))
    if margins is None:
        checks.append(_na("template.margin_floor", "critical", "FORMAT", None, "Margins could not be inferred."))
    else:
        low = [s for s, v in margins.items() if v < MIN_MARGIN_IN - _EPS]
        checks.append(_chk("template.margin_floor", "fail" if low else "pass", "critical", "FORMAT", margins,
                           {"min_in": MIN_MARGIN_IN},
                           f"Margin(s) below {MIN_MARGIN_IN} in: {', '.join(low)}." if low else ""))

    # ---- body font size (smallest text size used anywhere in the document)
    sizes = _font_sizes(src, base_pt)
    normal = _SIZE_TABLE[base_pt]["normalsize"]
    body_pt = round(min(sizes + [normal]), 2)
    want_body = _field(contract, "typography", "body", "size_pt")
    tol = _field(contract, "typography", "size_tolerance_pt")
    tol = float(tol) if isinstance(tol, (int, float)) else 0.1
    if want_body is None:
        checks.append(_na("template.body_font_size", "error", "TEMPLATE", body_pt, "Contract body size is unknown."))
    else:
        ok = abs(body_pt - float(want_body)) <= tol + _EPS
        checks.append(_chk("template.body_font_size", "pass" if ok else "fail", "error", "TEMPLATE", body_pt,
                           {"size_pt": want_body, "tolerance_pt": tol},
                           "" if ok else f"Smallest text size is {body_pt} pt, contract body size is {want_body} pt."))
    low_font = body_pt < MIN_BODY_PT - _EPS
    checks.append(_chk("template.body_font_floor", "fail" if low_font else "pass", "critical", "FORMAT", body_pt,
                       {"min_pt": MIN_BODY_PT},
                       f"Text set at {body_pt} pt, below the {MIN_BODY_PT:g} pt floor." if low_font else ""))

    # ---- forbidden structures / columns
    forbidden = _forbidden(src, _layout_macros(contract))
    layout = _field(contract, "layout")
    if forbidden:
        checks.append(_chk("template.forbidden_structures", "fail", "error", "FORMAT", forbidden, [],
                           f"Graphics/table/column/box structure(s) found: {', '.join(forbidden)}."))
    elif not isinstance(layout, dict):
        checks.append(_na("template.forbidden_structures", "error", "FORMAT", [], "Contract layout is unknown."))
    else:
        checks.append(_chk("template.forbidden_structures", "pass", "error", "FORMAT", [], []))

    columns = 1
    m = re.search(r"\\begin\s*\{multicols\*?\}\s*\{\s*(\d+)\s*\}", src)
    if m:
        columns = max(2, int(m.group(1)))
    elif re.search(r"\\twocolumn(?![A-Za-z])", src) or "twocolumn" in opts:
        columns = 2
    want_cols = _field(contract, "layout", "columns")
    if columns > 1:
        checks.append(_chk("template.columns", "fail", "error", "FORMAT", columns, want_cols if want_cols else 1,
                           f"Document uses {columns} columns; ATS-safe layout is single column."))
    elif want_cols is None:
        checks.append(_na("template.columns", "error", "FORMAT", columns, "Contract column count is unknown."))
    else:
        ok = columns == int(want_cols)
        checks.append(_chk("template.columns", "pass" if ok else "fail", "error", "FORMAT", columns, want_cols,
                           "" if ok else f"Document uses {columns} column(s), contract requires {want_cols}."))

    # ---- font family
    family, decorative = _font_family(src, packages)
    want_family = _field(contract, "typography", "font_family")
    if decorative:
        checks.append(_chk("template.font_family", "fail", "error", "FORMAT", {"family": family,
                           "decorative": decorative}, want_family,
                           f"Decorative/script font(s) loaded: {', '.join(decorative)}."))
    elif want_family is None:
        checks.append(_na("template.font_family", "error", "FORMAT", {"family": family}, "Contract font is unknown."))
    else:
        ok = family.lower() == str(want_family).lower() or (
            family.lower() in _CM_EQUIVALENT and str(want_family).lower() in _CM_EQUIVALENT)
        checks.append(_chk("template.font_family", "pass" if ok else "fail", "error", "FORMAT",
                           {"family": family}, want_family,
                           "" if ok else f"Font family is {family}, contract requires {want_family}."))

    # ---- name font size (\begin{center} block's size switch)
    center = _CENTER_RE.search(src)
    name_size_m = _SIZE_CMD_RE.search(center.group(1)) if center else None
    name_pt = _SIZE_TABLE[base_pt].get(name_size_m.group(1)) if name_size_m else None
    want_name = _field(contract, "typography", "name", "size_pt")
    if want_name is None or name_pt is None:
        checks.append(_na("template.name_font_size", "error", "TEMPLATE", name_pt,
                          "Contract name size is unknown." if want_name is None else
                          "No size-switch command found in the \\begin{center} header block."))
    else:
        ok = abs(name_pt - float(want_name)) <= tol + _EPS
        checks.append(_chk("template.name_font_size", "pass" if ok else "fail", "error", "TEMPLATE", name_pt,
                           {"size_pt": want_name, "tolerance_pt": tol},
                           "" if ok else f"Header name size is {name_pt} pt, contract requires {want_name} pt."))

    # ---- heading font size + heading style (\titleformat{\section} line)
    title_m = _TITLEFORMAT_SECTION_RE.search(src)
    title_line = title_m.group(1) if title_m else None
    heading_size_m = _SIZE_CMD_RE.search(title_line) if title_line else None
    heading_pt = _SIZE_TABLE[base_pt].get(heading_size_m.group(1)) if heading_size_m else None
    want_heading = _field(contract, "typography", "heading", "size_pt")
    if want_heading is None or heading_pt is None:
        checks.append(_na("template.heading_font_size", "error", "TEMPLATE", heading_pt,
                          "Contract heading size is unknown." if want_heading is None else
                          "No \\titleformat{\\section} size switch found."))
    else:
        ok = abs(heading_pt - float(want_heading)) <= tol + _EPS
        checks.append(_chk("template.heading_font_size", "pass" if ok else "fail", "error", "TEMPLATE", heading_pt,
                           {"size_pt": want_heading, "tolerance_pt": tol},
                           "" if ok else f"Section heading size is {heading_pt} pt, contract requires {want_heading} pt."))

    want_heading_style = _field(contract, "formatting", "heading_style")
    if want_heading_style is None:
        checks.append(_na("template.heading_style", "error", "FORMAT", title_line, "Contract heading style is unknown."))
    elif str(want_heading_style) not in _HEADING_STYLE_TOKENS:
        checks.append(_na("template.heading_style", "error", "FORMAT", title_line,
                          f"Unrecognized heading style {want_heading_style!r}; not asserting a guess."))
    elif title_line is None:
        checks.append(_chk("template.heading_style", "fail", "error", "FORMAT", None, want_heading_style,
                           "No \\titleformat{\\section} found."))
    else:
        required = _HEADING_STYLE_TOKENS[str(want_heading_style)]
        missing = [t for t in required if t not in title_line]
        checks.append(_chk("template.heading_style", "fail" if missing else "pass", "error", "FORMAT",
                           title_line, list(required),
                           f"Heading style is missing: {', '.join(missing)}." if missing else ""))

    # ---- spacing (bullet \setlist k=v pairs; no compressed line spacing)
    setlist_m = _SETLIST_RE.search(src)
    setlist_kv = dict(p.split("=", 1) for p in (setlist_m.group(1).split(",") if setlist_m else []) if "=" in p)
    setlist_kv = {k.strip(): v.strip() for k, v in setlist_kv.items()}
    want_bullet = _field(contract, "spacing", "bullet")
    want_line_spacing = _field(contract, "spacing", "line_spacing")
    spacing_problems = []
    if isinstance(want_bullet, str) and want_bullet:
        for pair in want_bullet.split(","):
            pair = pair.strip()
            if "=" not in pair:
                continue
            key, val = (x.strip() for x in pair.split("=", 1))
            if setlist_kv.get(key) != val:
                spacing_problems.append(f"\\setlist {key}={setlist_kv.get(key)!r}, contract requires {val!r}")
    linespread_m = _LINESPREAD_RE.search(src)
    if linespread_m and abs(float(linespread_m.group(1)) - 1.0) > _EPS:
        spacing_problems.append(f"\\linespread{{{linespread_m.group(1)}}} compresses/expands line spacing")
    if _BASELINESTRETCH_RE.search(src):
        spacing_problems.append("\\baselinestretch is redefined")
    spacing_cmd_m = _SPACING_CMDS_RE.search(src)
    if spacing_cmd_m:
        spacing_problems.append(f"\\{spacing_cmd_m.group(1)} changes line spacing away from single")
    if want_bullet is None and want_line_spacing is None:
        checks.append(_na("template.spacing", "error", "FORMAT", setlist_kv, "Contract spacing is unknown."))
    else:
        checks.append(_chk("template.spacing", "fail" if spacing_problems else "pass", "error", "FORMAT",
                           setlist_kv, {"bullet": want_bullet, "line_spacing": want_line_spacing},
                           "; ".join(spacing_problems) if spacing_problems else ""))

    # ---- bullet style (itemize only, no enumerate/description)
    has_itemize = bool(re.search(r"\\begin\{itemize\}", src))
    bad_lists = [e for e in ("enumerate", "description") if re.search(r"\\begin\{" + e + r"\}", src)]
    want_bullet_style = _field(contract, "formatting", "bullet_style")
    if want_bullet_style is None:
        checks.append(_na("template.bullet_style", "error", "FORMAT", {"itemize": has_itemize, "other": bad_lists},
                          "Contract bullet style is unknown."))
    else:
        ok = has_itemize and not bad_lists and str(want_bullet_style) == "itemize"
        checks.append(_chk("template.bullet_style", "pass" if ok else "fail", "error", "FORMAT",
                           {"itemize": has_itemize, "other": bad_lists}, want_bullet_style,
                           "" if ok else f"Expected only itemize lists; found other list type(s): {bad_lists}."
                           if bad_lists else "No itemize list found." if not has_itemize else ""))

    # ---- link style (hyperref hidelinks, warning-severity)
    hyperref_opts = next((opts for opts, name in packages if name == "hyperref"), None)
    want_link_style = _field(contract, "formatting", "link_style")
    if want_link_style is None:
        checks.append(_na("template.link_style", "warning", "FORMAT", hyperref_opts, "Contract link style is unknown."))
    elif hyperref_opts is None:
        checks.append(_chk("template.link_style", "fail", "warning", "FORMAT", None, want_link_style,
                           "No hyperref package loaded."))
    else:
        colorlinks = "colorlinks" in hyperref_opts or "colorlinks=true" in hyperref_opts
        hidelinks = "hidelinks" in hyperref_opts
        ok = hidelinks and not colorlinks
        checks.append(_chk("template.link_style", "pass" if ok else "fail", "warning", "FORMAT",
                           hyperref_opts, want_link_style,
                           "" if ok else ("colorlinks is set; links must be hidelinks." if colorlinks else
                                         "hyperref is missing the hidelinks option.")))

    inferred = {"page_size": paper, "base_font_pt": base_pt, "class_size_option": requested,
                "body_font_pt": body_pt, "margins_in": margins, "columns": columns,
                "forbidden": forbidden, "font_family": family, "name_font_pt": name_pt,
                "heading_font_pt": heading_pt, "bullet_spacing": setlist_kv}
    return checks, inferred
