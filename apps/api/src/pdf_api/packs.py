"""Reading-pack assembly: render each source, merge, bookmark, title page, TOC.

Pure rendering/merging logic. Credit charging, job status transitions and
refunds live in :mod:`pdf_api.jobservice` and :mod:`pdf_api.metering` so that
packs reuse exactly one billing and lifecycle implementation.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape

from pypdf import PdfReader, PdfWriter

from .logging_config import get_logger
from .renderer import BrowserPool
from .schemas import PackItem, RenderOptions

log = get_logger(__name__)

# The table of contents is rendered, measured, then re-rendered until its own
# page count stops changing, so the printed page numbers stay correct.
_TOC_MAX_PASSES = 4


class PackError(Exception):
    """A reading pack could not be assembled."""


class PackTooLargeError(PackError):
    """The merged pack exceeded the configured output limit."""


class PackItemError(PackError):
    """One pack source failed to render."""

    def __init__(self, index: int, title: str, cause: str) -> None:
        super().__init__(f"item {index + 1} ({title!r}) failed: {cause}")
        self.index = index
        self.title = title
        self.cause = cause


@dataclass
class RenderedItem:
    title: str
    pdf: bytes
    pages: int


@dataclass
class PackResult:
    pdf: bytes
    item_count: int
    total_pages: int


def _page_count(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


_BASE_CSS = """
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    color: #0f172a;
    margin: 0;
    padding: 0;
  }
"""


def title_page_html(pack_title: str, item_count: int, generated_at: datetime) -> str:
    """A single centred cover page."""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
{_BASE_CSS}
  .wrap {{
    height: 96vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: 0 12mm;
  }}
  h1 {{ font-size: 30pt; line-height: 1.2; margin: 0 0 10mm; }}
  .meta {{ font-size: 11pt; color: #475569; }}
  .rule {{ width: 40mm; height: 2px; background: #0f172a; margin: 0 0 10mm; }}
</style></head>
<body><div class="wrap">
  <div class="rule"></div>
  <h1>{escape(pack_title)}</h1>
  <div class="meta">{item_count} article{"s" if item_count != 1 else ""}</div>
  <div class="meta">{escape(generated_at.strftime("%d %B %Y"))}</div>
</div></body></html>"""


def toc_html(entries: list[tuple[str, int]]) -> str:
    """Table of contents. ``entries`` is ``(title, printed_page_number)``."""
    rows = "\n".join(
        f"""    <li class="row">
      <span class="t">{escape(title)}</span>
      <span class="dots"></span>
      <span class="p">{page}</span>
    </li>"""
        for title, page in entries
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
{_BASE_CSS}
  h2 {{ font-size: 20pt; margin: 0 0 8mm; }}
  ol {{ list-style: none; margin: 0; padding: 0; }}
  .row {{
    display: flex;
    align-items: baseline;
    gap: 2mm;
    font-size: 12pt;
    padding: 2.2mm 0;
    border-bottom: 1px solid #e2e8f0;
  }}
  .t {{ flex: 0 1 auto; }}
  .dots {{ flex: 1 1 auto; border-bottom: 1px dotted #94a3b8; transform: translateY(-3px); }}
  .p {{ flex: 0 0 auto; font-variant-numeric: tabular-nums; color: #334155; }}
</style></head>
<body>
  <h2>Contents</h2>
  <ol>
{rows}
  </ol>
</body></html>"""


async def _render_html(pool: BrowserPool, html: str, options: RenderOptions) -> bytes:
    result = await pool.render(kind="html", source=html, options=options)
    return result.pdf


async def render_items(
    pool: BrowserPool,
    items: list[PackItem],
    options: RenderOptions,
) -> list[RenderedItem]:
    """Render every source in the caller's order.

    A single failing source fails the whole pack so the caller is never charged
    for a silently incomplete document; the job refund path then restores the
    credits.
    """
    rendered: list[RenderedItem] = []
    for index, item in enumerate(items):
        title = item.display_title(index)
        try:
            result = await pool.render(kind=item.kind, source=item.source, options=options)
        except Exception as exc:
            raise PackItemError(index, title, str(exc)) from exc
        rendered.append(RenderedItem(title=title, pdf=result.pdf, pages=_page_count(result.pdf)))
    return rendered


def _toc_entries(rendered: list[RenderedItem], front_pages: int) -> list[tuple[str, int]]:
    entries: list[tuple[str, int]] = []
    cursor = front_pages
    for item in rendered:
        entries.append((item.title, cursor + 1))  # 1-based printed page number
        cursor += item.pages
    return entries


async def _build_front_matter(
    pool: BrowserPool,
    rendered: list[RenderedItem],
    options: RenderOptions,
    *,
    pack_title: str,
    want_title_page: bool,
    want_toc: bool,
) -> list[bytes]:
    """Render cover + contents, resolving TOC page numbers to a fixed point."""
    front: list[bytes] = []
    title_pdf: bytes | None = None
    title_pages = 0
    if want_title_page:
        title_pdf = await _render_html(
            pool,
            title_page_html(pack_title, len(rendered), datetime.now(UTC)),
            options,
        )
        title_pages = _page_count(title_pdf)
        front.append(title_pdf)

    if not want_toc:
        return front

    toc_pages = 1
    toc_pdf = b""
    for _ in range(_TOC_MAX_PASSES):
        entries = _toc_entries(rendered, title_pages + toc_pages)
        toc_pdf = await _render_html(pool, toc_html(entries), options)
        actual = _page_count(toc_pdf)
        if actual == toc_pages:
            break
        toc_pages = actual
    else:  # pragma: no cover - length oscillation is not expected
        log.warning("pack.toc_length_unstable", passes=_TOC_MAX_PASSES)

    front.append(toc_pdf)
    return front


def merge_with_bookmarks(front: list[bytes], rendered: list[RenderedItem]) -> bytes:
    """Concatenate front matter + items, adding one bookmark per item."""
    writer = PdfWriter()
    for pdf in front:
        for page in PdfReader(io.BytesIO(pdf)).pages:
            writer.add_page(page)

    for item in rendered:
        start_index = len(writer.pages)  # 0-based physical index
        for page in PdfReader(io.BytesIO(item.pdf)).pages:
            writer.add_page(page)
        writer.add_outline_item(item.title, start_index)

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


async def build_pack(
    pool: BrowserPool,
    *,
    items: list[PackItem],
    options: RenderOptions,
    pack_title: str,
    toc: bool,
    title_page: bool,
    max_output_bytes: int,
) -> PackResult:
    """Render every source in order and merge into one bookmarked PDF."""
    rendered = await render_items(pool, items, options)
    front = await _build_front_matter(
        pool,
        rendered,
        options,
        pack_title=pack_title,
        want_title_page=title_page,
        want_toc=toc,
    )
    merged = merge_with_bookmarks(front, rendered)
    if len(merged) > max_output_bytes:
        raise PackTooLargeError(f"pack output {len(merged)} bytes exceeds limit {max_output_bytes}")

    total_pages = sum(_page_count(pdf) for pdf in front) + sum(item.pages for item in rendered)
    return PackResult(pdf=merged, item_count=len(rendered), total_pages=total_pages)
