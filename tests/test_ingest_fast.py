"""pdf_to_text_fast on tiny generated PDFs (no network, no GROBID)."""

import pytest

from claimtrace.ingest import pdf_to_text_fast


def make_pdf(pages: list[list[tuple[int, str]]], title: str | None = None) -> bytes:
    """pages: per page, (font size, line) top to bottom, Helvetica. Minimal valid PDF."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", ""]  # pages tree filled in below
    kids = []
    for lines in pages:
        ops, y = [], 750
        for size, text in lines:
            ops.append(f"BT /F1 {size} Tf 72 {y} Td ({text}) Tj ET")
            y -= size + 8
        stream = "\n".join(ops)
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        content = len(objs)
        objs.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents "
            f"{content} 0 R /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
            "/BaseFont /Helvetica >> >> >> >>"
        )
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    info = None
    if title is not None:
        objs.append(f"<< /Title ({title}) >>")
        info = len(objs)
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    trailer = f"/Size {len(objs) + 1} /Root 1 0 R" + (f" /Info {info} 0 R" if info else "")
    out += f"trailer\n<< {trailer} >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


HEAD = (9, "Journal of Bone Research 12 (2020)")


def paper(n_pages: int = 3, title: str | None = None) -> bytes:
    first = [HEAD, (20, "Vitamin D and fracture risk"), (20, "in older adults"), (10, "J. Smith")]
    first += [(10, "Hip fractures fell by 10 5 per year."), (9, "1")]
    rest = [[HEAD, (10, f"Results on page {i}."), (9, str(i))] for i in range(2, n_pages + 1)]
    return make_pdf([first, *rest], title)


async def test_title_from_largest_font_goes_first_and_running_head_is_dropped():
    text = await pdf_to_text_fast(paper())
    lines = text.split("\n")
    assert lines[0] == "Vitamin D and fracture risk in older adults"
    assert "Journal of Bone Research" not in text  # on every page: running head
    assert "Hip fractures fell by 10 5 per year." in lines  # verbatim, no markers invented
    assert "Results on page 3." in lines and "3" in lines  # page numbers differ: kept


async def test_running_heads_kept_on_short_documents():
    text = await pdf_to_text_fast(paper(n_pages=2))
    assert "Journal of Bone Research 12 (2020)" in text


async def test_metadata_title_only_when_page_one_carries_it():
    text = await pdf_to_text_fast(paper(title="Vitamin D and fracture risk in older adults"))
    assert text.startswith("Vitamin D and fracture risk in older adults\n\n")
    text = await pdf_to_text_fast(paper(title="Microsoft Word - draft7.docx"))
    assert "Microsoft Word" not in text
    assert text.startswith("Vitamin D and fracture risk in older adults\n\n")


@pytest.mark.parametrize("pdf", [b"%PDF-1.4 not really a pdf", make_pdf([[]])])
async def test_unreadable_or_textless_pdf_raises(pdf):
    with pytest.raises(Exception):  # noqa: B017 - the caller treats any exception as "no text"
        await pdf_to_text_fast(pdf)
