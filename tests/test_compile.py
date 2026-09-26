"""Exact-LaTeX compilation (spec §44): the .tex returned is the .tex compiled."""

from __future__ import annotations

import pytest

from lib import export
from lib.errors import ResumeTailorError
from lib.ids import normalize_master
from lib.latex import render_latex
from lib.locking import sha256_file, sha256_text

try:
    export._tectonic_path()
    _HAVE_TECTONIC = True
except ResumeTailorError:
    _HAVE_TECTONIC = False

pytestmark = pytest.mark.skipif(not _HAVE_TECTONIC, reason="tectonic not available")


@pytest.fixture(scope="module")
def synthetic_doc():
    from conftest import SYNTHETIC_LEGACY_MASTER
    return normalize_master(SYNTHETIC_LEGACY_MASTER, "resume")


@pytest.fixture(scope="module")
def compiled(tmp_path_factory, synthetic_doc):
    tex = render_latex(synthetic_doc, "classic-minimalist")
    out = tmp_path_factory.mktemp("compile")
    return tex, export.compile_tex(tex, out, "synthetic")


def test_compile_writes_exact_tex_and_pdf(compiled):
    tex, info = compiled
    from pathlib import Path
    assert info["tex_sha256"] == sha256_text(tex)
    assert sha256_file(Path(info["tex_path"])) == sha256_text(tex)
    assert Path(info["tex_path"]).read_bytes() == tex.encode("utf-8")
    pdf = Path(info["pdf_path"])
    assert pdf.exists() and pdf.read_bytes()[:5] == b"%PDF-"
    assert info["pdf_sha256"] == sha256_file(pdf)
    assert isinstance(info["overfull_hbox_count"], int)
    assert isinstance(info["underfull_hbox_count"], int)
    assert len(info["log"]) <= 20_000


@pytest.mark.parametrize("bad", ["../x", "a/b", "a\\b", "..", ""])
def test_bad_basename_rejected(tmp_path, bad):
    with pytest.raises(ResumeTailorError) as ei:
        export.compile_tex("\\documentclass{article}\\begin{document}x\\end{document}", tmp_path, bad)
    assert ei.value.code in ("PATH_TRAVERSAL", "INVALID_ID")
    assert not (tmp_path.parent / "x.tex").exists()


def test_broken_latex_raises_compile_failed(tmp_path):
    broken = "\\documentclass{article}\n\\begin{document}\n\\undefinedmacroxyz{oops}\n"
    with pytest.raises(ResumeTailorError) as ei:
        export.compile_tex(broken, tmp_path, "broken")
    assert ei.value.code == "LATEX_COMPILE_FAILED"
    assert len(ei.value.details["log_tail"]) <= 2_000
    assert not (tmp_path / "broken.pdf").exists()


def test_to_pdf_still_works(tmp_path, synthetic_doc):
    out = tmp_path / "exports" / "v1.pdf"
    path = export.to_pdf(synthetic_doc, str(out), "classic-minimalist")
    assert path == str(out)
    assert out.exists() and out.read_bytes()[:5] == b"%PDF-"
    tex_path = out.with_suffix(".tex")
    assert tex_path.read_text(encoding="utf-8") == export.to_tex(synthetic_doc, "classic-minimalist")


def test_overfull_count_uses_final_pass_only(tmp_path):
    # \label/\ref forces tectonic to rerun TeX, which repeats every box warning.
    tex = ("\\documentclass{article}\n\\begin{document}\n\\section{A}\\label{s}See \\ref{s}.\n\n"
           "\\noindent\\hbox to 1in{\\hbox to 2in{x\\hfil}}\n\\end{document}\n")
    info = export.compile_tex(tex, tmp_path, "overfull")
    assert "Rerunning TeX" in info["log"]
    assert info["log"].count("Overfull \\hbox") == 2
    assert info["overfull_hbox_count"] == 1
