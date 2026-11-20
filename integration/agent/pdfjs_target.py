"""Pinned PDF.js viewer and independent verification of native Find behavior."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode

from browser_support import BrowserTarget, serve_directory

from vibe_maa.targets.archive import provision_archive

QUERY = "CEDAR LANTERN"
FINAL_HEADING = "Final Release Notes"
OBJECTIVE = (
    f"Use the PDF.js viewer's Find in document feature to search for `{QUERY}`. "
    "Select the second of the two matches, under the `Final Release Notes` heading, "
    "and leave Find open with that match selected. The page number can vary between documents."
)
ORACLE = "PDF.js find state, visible selected highlight, and real text of the current PDF page"

_UPSTREAM = {
    "name": "Mozilla PDF.js",
    "repository": "https://github.com/mozilla/pdf.js",
    "release": "v6.5.438",
    "commit": "0a4153349dc473ba950fc17941c5ca7eda43c1de",
    "archive_url": "https://github.com/mozilla/pdf.js/releases/download/v6.5.438/pdfjs-6.5.438-dist.zip",
    "archive_sha256": "fcbe9ba21d9911843e3502adc178a7f549a524a4055f887bedc942e5490c6ba1",
    "archive_size": 6540268,
    "license": "Apache-2.0",
    "license_url": "https://github.com/mozilla/pdf.js/blob/v6.5.438/LICENSE",
}

_COVER = ("Change Control Packet", "Deployment notes and supporting material.")
_DRAFT = ("Working Draft", f"Proposed deployment code: {QUERY}.")
_CHECKLIST = ("Validation Checklist", "Confirm the CEDAR LINE run has no open tasks.")
_FINAL = (FINAL_HEADING, f"Approved deployment code: {QUERY}.")
_DOCUMENTS = {
    "release": (_COVER, _DRAFT, _CHECKLIST, _FINAL),
    "extended-release": (
        _COVER,
        ("Reference Appendix", "Prior notes are provided for context."),
        _DRAFT,
        _CHECKLIST,
        _FINAL,
    ),
}
VARIANTS = tuple(_DOCUMENTS)


def _fixture_pdf(pages: tuple[tuple[str, str], ...]) -> bytes:
    """Build a small text PDF without a fixture toolchain or bundled upstream sample."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    page_refs = []
    for heading, body in pages:
        page_number = len(objects) + 1
        page_refs.append(f"{page_number} 0 R")
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                "/Resources << /Font << /F1 3 0 R >> >> "
                f"/Contents {page_number + 1} 0 R >>"
            ).encode("ascii")
        )
        content = (
            f"BT /F1 26 Tf 72 704 Td ({heading}) Tj /F1 16 Tf 0 -40 Td ({body}) Tj ET\n"
        ).encode("ascii")
        objects.append(f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream")
    objects[1] = (f"<< /Type /Pages /Kids [{' '.join(page_refs)}] /Count {len(pages)} >>").encode(
        "ascii"
    )
    result = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for index, value in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode() + value + b"\nendobj\n")
    start = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    )
    return bytes(result)


_FIXTURES = {variant: _fixture_pdf(pages) for variant, pages in _DOCUMENTS.items()}


@contextmanager
def launch(cache: Path, variant: str = VARIANTS[0]) -> Iterator[tuple[str, dict[str, Any]]]:
    document_path = "/fixtures/release.pdf"
    webroot = provision_archive(
        cache,
        target="pdfjs",
        metadata=_UPSTREAM,
        archive_suffix=".zip",
        required_files=("web/viewer.html", "web/viewer.mjs", "build/pdf.worker.mjs"),
    )
    metadata = {
        **_UPSTREAM,
        "fixture_sha256": {
            name: hashlib.sha256(document).hexdigest() for name, document in _FIXTURES.items()
        },
    }
    assets = {document_path: ("application/pdf", _FIXTURES[variant])}
    with serve_directory(webroot, assets=assets) as origin:
        yield f"{origin}/web/viewer.html?{urlencode({'file': document_path})}", metadata


def ready(page: Any) -> None:
    page.wait_for_function(
        """() => {
            const app = window.PDFViewerApplication;
            return app?.pdfDocument?.numPages > 0 && app.pdfViewer.pageViewsReady &&
                !!document.querySelector('.page[data-page-number="1"] .textLayer span');
        }""",
        timeout=10000,
    )


def snapshot(page: Any) -> dict[str, Any]:
    return page.evaluate("""async () => {
        const { pdfDocument, pdfViewer, findController, findBar } = window.PDFViewerApplication;
        const pageNumber = pdfViewer.currentPageNumber;
        const selected = findController.selected;
        const matches = Array.from({ length: pdfDocument.numPages }, (_, index) =>
            findController.pageMatches[index]?.length || 0);
        const pdfPage = await pdfDocument.getPage(pageNumber);
        const pageText = await pdfPage.getTextContent();
        const viewport = document.getElementById('viewerContainer').getBoundingClientRect();
        const highlights = document.querySelectorAll(
            `.page[data-page-number="${pageNumber}"] .highlight.selected`
        );
        return {
            page_number: pageNumber,
            page_count: pdfDocument.numPages,
            page_heading: pageText.items.find(item => item.str)?.str || '',
            query: findController.state?.query || '',
            find_open: findBar.opened,
            matches_by_page: matches,
            selected_page: selected.pageIdx + 1,
            selected_match_on_page: selected.matchIdx,
            visible_selected_highlight: Array.from(highlights).some(node => {
                const r = node.getBoundingClientRect();
                return r.width > 0 && r.height > 0 && r.right > viewport.left &&
                    r.left < viewport.right && r.bottom > viewport.top && r.top < viewport.bottom;
            }),
        };
    }""")


def completed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    matching_pages = [
        number for number, matches in enumerate(after["matches_by_page"], start=1) if matches
    ]
    return (
        before["query"] == ""
        and before["find_open"] is False
        and before["page_number"] == 1
        and after["query"].casefold() == QUERY.casefold()
        and after["find_open"] is True
        and sum(after["matches_by_page"]) == len(matching_pages) == 2
        and after["page_number"] == after["selected_page"] == matching_pages[1]
        and after["selected_match_on_page"] == 0
        and after["page_heading"] == FINAL_HEADING
        and after["visible_selected_highlight"] is True
    )


TARGET = BrowserTarget(OBJECTIVE, ORACLE, launch, ready, snapshot, completed, variants=VARIANTS)
