"""Hand-assembled PDF bytes for Tier A of lib/validators/pdf.py's tests.

Not a test module -- a helper. Every function builds a PDF object graph by
hand (own xref table, own object offsets) so the same call always produces
the same bytes, and check_pdf's *measured* path (pypdf for
integrity/page-count/page-size/fonts, pdfplumber for character-level
text/position/size) runs against it exactly as it would against a real
compiled PDF.

Why hand-built rather than compiled with tectonic: a few branches are
essentially unreachable from LaTeX output. The clearest example is a
*non-embedded* base-14 font (`minimal_pdf`) -- tectonic always embeds the
fonts it uses, so there is no tex source that reliably produces a PDF
referencing e.g. /Helvetica with no embedded font program. Hand-assembling
the object graph is the only way to exercise that branch deterministically.

These PDFs are minimal on purpose: one Type1 base-14 font (no embedding, no
subsetting), simple content streams (BT/Tf/Td/Tj/ET or a single image XObject
+ cm/Do), and a plain classic xref table. No binary fixture files are
committed anywhere -- everything here is generated at test time from source
you can read and diff.
"""

from __future__ import annotations

import io

_PAGE_SIZES_PT = {"letter": (612.0, 792.0), "a4": (595.0, 842.0)}


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _obj_bytes(num: int, body: bytes) -> bytes:
    return f"{num} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"


def _assemble(objects: dict[int, bytes]) -> bytes:
    """objects: {object_number: body_bytes} for a contiguous 1..N range.
    Builds a full PDF with a classic (non-cross-reference-stream) xref table
    and a trailer pointing at object 1 as /Root."""
    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    out = bytearray(header)
    n = max(objects)
    offsets = [0] * (n + 1)
    for num in range(1, n + 1):
        offsets[num] = len(out)
        out += _obj_bytes(num, objects[num])
    xref_offset = len(out)
    out += f"xref\n0 {n + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for num in range(1, n + 1):
        out += f"{offsets[num]:010d} 00000 n \n".encode("latin-1")
    out += (f"trailer\n<< /Size {n + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF").encode("latin-1")
    return bytes(out)


def minimal_pdf(pages: int = 1, size: str | tuple[float, float] = "letter",
                 text: str | list[str] = "Experience Summary Skills Education test resume content.",
                 base_font: str = "Helvetica", font_size: float = 10.0,
                 x: float = 72.0, y: float | None = None,
                 blank_pages: "set[int] | None" = None,
                 page_sizes: "list[tuple[float, float]] | None" = None) -> bytes:
    """A single- or multi-page PDF, one Type1 base-14 font (never embedded --
    see module docstring), one line of text per page positioned at (x, y).

    blank_pages: 1-indexed page numbers to leave with an empty content
    stream (no text at all) instead of `text`.
    page_sizes: per-page (width_pt, height_pt) overrides (for a mixed-size
    document); `size` is the fallback for any page not listed.
    """
    blank_pages = blank_pages or set()
    default_w, default_h = size if isinstance(size, tuple) else _PAGE_SIZES_PT[size]
    page_texts = text if isinstance(text, list) else [text] * pages

    objects: dict[int, bytes] = {}
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    font_num = 3
    objects[font_num] = (f"<< /Type /Font /Subtype /Type1 /BaseFont /{base_font} "
                         "/Encoding /WinAnsiEncoding >>").encode("latin-1")

    next_num = 4
    page_nums: list[int] = []
    for i in range(pages):
        page_num, content_num = next_num, next_num + 1
        next_num += 2
        page_nums.append(page_num)

        w, h = default_w, default_h
        if page_sizes and i < len(page_sizes):
            w, h = page_sizes[i]
        py = y if y is not None else h - 100.0

        if (i + 1) in blank_pages:
            stream = b""
        else:
            txt = page_texts[i] if i < len(page_texts) else page_texts[-1]
            stream = f"BT /F1 {font_size} Tf {x} {py} Td ({_escape(txt)}) Tj ET".encode("latin-1")
        objects[content_num] = (f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream
                                + b"\nendstream")
        objects[page_num] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w:g} {h:g}] "
                             f"/Resources << /Font << /F1 {font_num} 0 R >> >> "
                             f"/Contents {content_num} 0 R >>").encode("latin-1")

    kids = " ".join(f"{n} 0 R" for n in page_nums)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode("latin-1")
    return _assemble(objects)


def two_column_pdf(lines: int = 12, size: str = "letter", left_x: float = 60.0,
                    right_x: float = 340.0, font_size: float = 10.0) -> bytes:
    """A single page whose text is laid out in two side-by-side columns --
    enough lines, each with a right-hand run starting at a consistent x, to
    trip check_pdf's heuristic `pdf.columns` multi-column warning."""
    w, h = _PAGE_SIZES_PT[size]
    parts = []
    top = h - 100.0
    for i in range(lines):
        row_y = top - i * (font_size + 6)
        parts.append(f"BT /F1 {font_size} Tf {left_x} {row_y} Td (Left column line {i}) Tj ET")
        parts.append(f"BT /F1 {font_size} Tf {right_x} {row_y} Td (Right column data {i}) Tj ET")
    stream = "\n".join(parts).encode("latin-1")
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        5: f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream",
    }
    objects[4] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w:g} {h:g}] "
                 "/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>").encode("latin-1")
    return _assemble(objects)


def image_only_pdf(size: str = "letter", img_w: int = 200, img_h: int = 200) -> bytes:
    """A single page containing only an image XObject -- no text operators
    at all. Trips pdf.text_extractable, pdf.empty_pages and pdf.margins
    simultaneously: the scanned-resume case (a photo/scan with no real
    text layer)."""
    w, h = _PAGE_SIZES_PT[size]
    raw = bytes([210, 210, 210]) * (img_w * img_h)  # flat light-gray RGB, uncompressed
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        3: (f"<< /Type /XObject /Subtype /Image /Width {img_w} /Height {img_h} "
           f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length {len(raw)} >>\nstream\n").encode("latin-1")
           + raw + b"\nendstream",
    }
    content = f"q {img_w} 0 0 {img_h} 50 50 cm /Im0 Do Q".encode("latin-1")
    objects[5] = f"<< /Length {len(content)} >>\nstream\n".encode("latin-1") + content + b"\nendstream"
    objects[4] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w:g} {h:g}] "
                 "/Resources << /XObject << /Im0 3 0 R >> >> /Contents 5 0 R >>").encode("latin-1")
    return _assemble(objects)


def encrypted_pdf(source: bytes, password: str = "secret") -> bytes:
    """`source`'s pages, re-saved password-encrypted via pypdf (real RC4/AES
    encryption is delegated to the library that already ships with this
    project rather than hand-rolled -- there is no determinism or
    reviewability benefit to reimplementing PDF crypto by hand)."""
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(source))
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(password)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def truncated_pdf(source: bytes, keep_fraction: float = 0.6) -> bytes:
    """`source` cut off partway through -- no xref/trailer survives, so any
    reader must fail integrity rather than parse a partial document."""
    return source[: int(len(source) * keep_fraction)]


def png_1x1() -> bytes:
    """The smallest possible valid PNG: one white pixel."""
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xdc\xccY\xe7"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
