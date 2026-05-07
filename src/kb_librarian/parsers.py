"""Local ingest parsers for non-markdown source formats."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from kb_librarian.errors import IngestError


SKIPPED_HTML_TAGS = {"script", "style", "noscript", "template", "svg", "nav", "header", "footer", "aside"}
BLOCK_HTML_TAGS = {
    "address",
    "article",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "main",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}


def parse_pdf(path: Path) -> tuple[str, dict[str, Any]]:
    """Extract text from a local text-backed PDF without OCR."""

    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - depends on packaging environment
        raise IngestError("PDF parsing requires the `pypdf` package. Install KB Librarian dependencies.") from exc

    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # pypdf exposes several parser exception classes.
        raise IngestError(f"Could not read PDF {path}: {exc}") from exc

    page_sections: list[str] = []
    page_numbers: list[int] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            extracted = page.extract_text() or ""
        except Exception as exc:
            raise IngestError(f"Could not extract text from PDF {path} page {index}: {exc}") from exc
        text = _normalize_document_text(extracted).strip()
        if not text:
            continue
        page_sections.append(f"[PDF page {index}]\n{text}")
        page_numbers.append(index)

    if not page_sections:
        raise IngestError(
            f"Could not extract text from PDF {path}; no text was found. "
            "Image-only PDFs require OCR, which is not supported."
        )

    metadata: dict[str, Any] = {
        "format": "pdf",
        "parser": "pypdf",
        "page_count": len(reader.pages),
        "pages": page_numbers,
    }
    return "\n\n".join(page_sections) + "\n", metadata


def parse_html(path: Path) -> tuple[str, dict[str, Any]]:
    """Extract visible text and local source metadata from an HTML file."""

    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise IngestError(f"Could not decode {path} as UTF-8 HTML.") from exc

    parser = _VisibleHTMLParser()
    try:
        parser.feed(raw)
        parser.close()
    except Exception as exc:
        raise IngestError(f"Could not parse HTML {path}: {exc}") from exc

    text = parser.render_text()
    if not text.strip():
        raise IngestError(f"Could not extract visible text from HTML {path}.")

    metadata: dict[str, Any] = {"format": "html", "parser": "html.parser"}
    if parser.title:
        metadata["title"] = parser.title
    if parser.source_url:
        metadata["source_url"] = parser.source_url
    if parser.links:
        base = parser.source_url or parser.base_href or ""
        metadata["links"] = [
            {
                "text": link["text"],
                "href": urljoin(base, link["href"]) if base else link["href"],
            }
            for link in parser.links[:50]
        ]
    return text, metadata


def _normalize_document_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip() + ("\n" if normalized.strip() else "")


class _VisibleHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_stack: list[str] = []
        self._title_parts: list[str] = []
        self._in_title = False
        self._current_link: dict[str, Any] | None = None
        self._links: list[dict[str, str]] = []
        self.base_href: str | None = None
        self.source_url: str | None = None

    @property
    def title(self) -> str | None:
        title = _collapse_inline_whitespace(" ".join(self._title_parts)).strip()
        return title or None

    @property
    def links(self) -> list[dict[str, str]]:
        return list(self._links)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = {name.lower(): value or "" for name, value in attrs}
        if tag == "title":
            self._in_title = True
        if tag == "base" and attr_map.get("href") and self.base_href is None:
            self.base_href = attr_map["href"].strip()
        if tag == "link" and self.source_url is None and attr_map.get("href"):
            rels = {item.strip().lower() for item in attr_map.get("rel", "").split()}
            if "canonical" in rels:
                self.source_url = attr_map["href"].strip()
        if tag == "meta" and self.source_url is None:
            name = (attr_map.get("name") or attr_map.get("property") or "").strip().lower()
            if name in {"og:url", "twitter:url", "source", "source_url"} and attr_map.get("content"):
                self.source_url = attr_map["content"].strip()

        if tag in SKIPPED_HTML_TAGS or _is_hidden(attr_map):
            self._skip_stack.append(tag)
            return
        if self._skip_stack:
            return

        if tag in BLOCK_HTML_TAGS:
            self._append_newline()
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            self._chunks.append("#" * level + " ")
        elif tag == "li":
            self._chunks.append("- ")
        elif tag == "a" and attr_map.get("href"):
            self._current_link = {"href": attr_map["href"].strip(), "parts": []}

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if self._skip_stack:
            if tag == self._skip_stack[-1]:
                self._skip_stack.pop()
            return
        if tag == "a" and self._current_link is not None:
            text = _collapse_inline_whitespace(" ".join(self._current_link["parts"])).strip()
            href = str(self._current_link["href"]).strip()
            if href:
                self._links.append({"text": text or href, "href": href})
            self._current_link = None
        if tag in BLOCK_HTML_TAGS:
            self._append_newline()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._skip_stack:
            return
        text = _collapse_inline_whitespace(data)
        if not text:
            return
        if self._current_link is not None:
            self._current_link["parts"].append(text)
        if self._chunks and not self._chunks[-1].endswith(("\n", " ")):
            self._chunks.append(" ")
        self._chunks.append(text)

    def render_text(self) -> str:
        body = _normalize_document_text("".join(self._chunks))
        title = self.title
        if title and not body.lstrip().startswith("# "):
            return f"# {title}\n\n{body}"
        return body

    def _append_newline(self) -> None:
        if not self._chunks or self._chunks[-1].endswith("\n"):
            return
        self._chunks.append("\n")


def _is_hidden(attrs: dict[str, str]) -> bool:
    if "hidden" in attrs:
        return True
    if attrs.get("aria-hidden", "").strip().lower() == "true":
        return True
    style = attrs.get("style", "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def _collapse_inline_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
