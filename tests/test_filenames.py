"""Deterministic export filenames (spec §46)."""

from __future__ import annotations

import unicodedata

import pytest

from lib.errors import ResumeTailorError
from lib.filenames import (ALLOWED_EXTS, MAX_STEM_CHARS, deterministic_filename,
                           role_from_jd_title)


def stem(fn: str) -> str:
    return fn.rsplit(".", 1)[0]


def test_basic_shape():
    assert deterministic_filename("Alex Example", "Backend Engineer", "resume", "pdf") == \
        "Alex_Example_Backend_Engineer_Resume.pdf"
    assert deterministic_filename("Alex Example", "Research Scientist", "cv", "pdf") == \
        "Alex_Example_Research_Scientist_CV.pdf"


def test_no_role():
    assert deterministic_filename("Alex Example", None, "resume", "pdf") == "Alex_Example_Resume.pdf"
    assert deterministic_filename("Alex Example", "  ", "cv", "tex") == "Alex_Example_CV.tex"


def test_middle_names_kept():
    assert deterministic_filename("Mary Jane Van Der Berg", "SWE", "resume", "pdf") == \
        "Mary_Jane_Van_Der_Berg_SWE_Resume.pdf"


@pytest.mark.parametrize("name", ["", None, "   ", "///", "\x00\x01"])
def test_empty_name(name):
    assert deterministic_filename(name, None, "resume", "pdf") == "Resume.pdf"
    assert deterministic_filename(name, None, "cv", "pdf") == "CV.pdf"
    assert deterministic_filename(name, "Data Engineer", "resume", "pdf") == "Data_Engineer_Resume.pdf"


def test_forbidden_characters_and_whitespace_become_underscore():
    fn = deterministic_filename('A/B\\C:D*E?F"G<H>I|J', "x\ty\nz\x00w", "resume", "pdf")
    assert fn == "A_B_C_D_E_F_G_H_I_J_x_y_z_w_Resume.pdf"
    for ch in '/\\:*?"<>|\t\n\x00 ':
        assert ch not in fn


def test_collapse_and_strip():
    assert deterministic_filename("__..Alex   Example..__", "  -- Lead //  Dev  ", "resume", "md") == \
        "Alex_Example_--_Lead_Dev_Resume.md"
    assert "__" not in deterministic_filename("A _ _ B", "C   D", "resume", "pdf")


def test_path_traversal_cannot_escape():
    fn = deterministic_filename("../../etc/passwd", "../x", "resume", "pdf")
    assert "/" not in fn and "\\" not in fn
    assert not fn.startswith(".")
    assert fn == "etc_passwd_x_Resume.pdf"


def test_unicode_letters_preserved():
    assert deterministic_filename("José María García", "Ingeniero", "cv", "pdf") == \
        "José_María_García_Ingeniero_CV.pdf"
    assert deterministic_filename("Zoë Østergaard", None, "resume", "pdf") == "Zoë_Østergaard_Resume.pdf"
    assert deterministic_filename("李 小龙", None, "resume", "pdf") == "李_小龙_Resume.pdf"


def test_nfkc_normalization():
    decomposed = "José"  # e + combining acute
    assert deterministic_filename(decomposed, None, "resume", "pdf") == "José_Resume.pdf"
    assert unicodedata.is_normalized("NFKC", deterministic_filename(decomposed, None, "resume", "pdf"))
    # Fullwidth letters and ligatures fold to ASCII under NFKC.
    assert deterministic_filename("Ａｌｅｘ", "ﬁnance", "resume", "pdf") == "Alex_finance_Resume.pdf"


def test_emoji_and_symbols_dropped():
    assert deterministic_filename("Alex 🚀 Example", "Dev™ & Ops", "resume", "pdf") == \
        "Alex_Example_DevTM_Ops_Resume.pdf"  # NFKC folds ™ to TM


@pytest.mark.parametrize("ext", ALLOWED_EXTS)
def test_allowed_exts(ext):
    assert deterministic_filename("A B", None, "resume", ext).endswith(f"_Resume.{ext}")


@pytest.mark.parametrize("ext", ["exe", "sh", "pdf/../x", "", "html", "PDF.exe"])
def test_disallowed_exts(ext):
    with pytest.raises(ValueError):
        deterministic_filename("A B", None, "resume", ext)


def test_ext_leading_dot_and_case_normalized():
    assert deterministic_filename("A B", None, "resume", ".PDF") == "A_B_Resume.pdf"


@pytest.mark.parametrize("kind", ["letter", "", None, "résumé"])
def test_invalid_kind(kind):
    with pytest.raises(ResumeTailorError) as ei:
        deterministic_filename("A B", None, kind, "pdf")
    assert ei.value.code == "INVALID_KIND"


def test_collision_suffix():
    base = deterministic_filename("Alex Example", "SWE", "resume", "pdf")
    a = deterministic_filename("Alex Example", "SWE", "resume", "pdf", collision_suffix="ver_01ABC")
    b = deterministic_filename("Alex Example", "SWE", "resume", "pdf", collision_suffix="ver_02XYZ")
    assert a == "Alex_Example_SWE_Resume_ver_01ABC.pdf"
    assert len({base, a, b}) == 3


@pytest.mark.parametrize("bad", ["", "///", "x" * 82])
def test_bad_collision_suffix(bad):
    with pytest.raises(ResumeTailorError) as ei:
        deterministic_filename("A B", None, "resume", "pdf", collision_suffix=bad)
    assert ei.value.code == "INVALID_ID"


def test_deterministic_and_no_decorations():
    args = ("Alex Example", "Backend Engineer", "resume", "pdf")
    outs = {deterministic_filename(*args) for _ in range(20)}
    assert len(outs) == 1
    fn = outs.pop()
    low = fn.lower()
    assert "final" not in low and "_v2" not in low and "copy" not in low
    assert not any(ch.isdigit() for ch in fn)  # no timestamps / counters


def test_length_trims_role_first():
    name = "Alexandra Example"
    role = "Principal Distributed Systems Engineer " * 10
    fn = deterministic_filename(name, role, "resume", "pdf")
    s = stem(fn)
    assert len(s) <= MAX_STEM_CHARS
    assert s.startswith("Alexandra_Example_Principal")
    assert s.endswith("_Resume")
    assert "__" not in s and not s.endswith("__Resume")


def test_length_then_trims_name_and_keeps_label_and_suffix():
    name = " ".join(["Bartholomew"] * 30)
    fn = deterministic_filename(name, "Engineer", "cv", "pdf", collision_suffix="ver_01ABC")
    s = stem(fn)
    assert len(s) <= MAX_STEM_CHARS
    assert s.endswith("_CV_ver_01ABC")
    assert "Engineer" not in s  # role was dropped before the name was trimmed
    assert s.startswith("Bartholomew_Bartholomew")


def test_length_cap_exact_boundary():
    name = "A" * 93  # 93 + len("_Resume") == 100
    assert stem(deterministic_filename(name, None, "resume", "pdf")) == name + "_Resume"
    long = deterministic_filename("A" * 94, None, "resume", "pdf")
    assert len(stem(long)) <= MAX_STEM_CHARS


def test_multibyte_filename_within_byte_limit():
    fn = deterministic_filename("李" * 300, "工程师" * 100, "resume", "pdf")
    assert len(stem(fn)) <= MAX_STEM_CHARS
    assert len(fn.encode("utf-8")) <= 255


def test_huge_input_is_bounded():
    fn = deterministic_filename("x " * 100_000, "y " * 100_000, "resume", "pdf")
    assert len(stem(fn)) <= MAX_STEM_CHARS


# --- role_from_jd_title -------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Senior Backend Engineer", "Senior Backend Engineer"),
    ("Job Description: Data Scientist", "Data Scientist"),
    ("Data Scientist - Job Description", "Data Scientist"),
    ("Software Engineer (Austin, TX)", "Software Engineer"),
    ("Software Engineer (Remote)", "Software Engineer"),
    ("Software Engineer (Backend)", "Software Engineer (Backend)"),
    ("Staff Engineer at Acme Corp", "Staff Engineer"),
    ("ML Engineer @ Beta Labs (Hybrid)", "ML Engineer"),
    ("Job Description - Platform Engineer (San Francisco, CA) at Example Inc", "Platform Engineer"),
    ("", ""),
])
def test_role_from_jd_title(title, expected):
    assert role_from_jd_title(title) == expected


def test_role_feeds_filename():
    role = role_from_jd_title("Job Description: Site Reliability Engineer (Remote) at Acme")
    assert deterministic_filename("Alex Example", role, "resume", "pdf") == \
        "Alex_Example_Site_Reliability_Engineer_Resume.pdf"


def test_longest_valid_version_id_as_suffix_fits():
    vid = "v" + "a" * 80  # longest id lib.workspace accepts
    fn = deterministic_filename("Alexandra Example", "Backend Engineer", "resume", "pdf", collision_suffix=vid)
    assert stem(fn).endswith(f"_Resume_{vid}")
    assert len(stem(fn)) <= MAX_STEM_CHARS
