from __future__ import annotations
from unittest.mock import MagicMock, patch

import pytest

from kb_librarian.errors import IngestError
from kb_librarian.parsers import _VisibleHTMLParser, _is_hidden, parse_html, parse_pdf


def _html_file(tmp_path, content: str, encoding: str = "utf-8"):
    path = tmp_path / "test.html"
    path.write_bytes(content.encode(encoding))
    return path


def _make_pdf(tmp_path, pages: list[str]) -> "Path":
    from pypdf import PdfWriter
    from pypdf.generic import NameObject
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        if text:
            # Add a simple text content stream so pypdf can extract it.
            content = f"BT /F1 12 Tf 100 700 Td ({text}) Tj ET".encode()
            from pypdf.generic import ContentStream
            page.replace_contents(content)
    path = tmp_path / "test.pdf"
    with open(path, "wb") as f:
        writer.write(f)
    return path


def test_parse_pdf_raises_when_all_pages_empty(tmp_path):
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    path = tmp_path / "blank.pdf"
    with open(path, "wb") as f:
        writer.write(f)
    with pytest.raises(IngestError, match="no text was found"):
        parse_pdf(path)


def test_parse_pdf_raises_on_corrupt_file(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"not a real pdf")
    with pytest.raises(IngestError, match="Could not read PDF"):
        parse_pdf(path)


def test_parse_pdf_skips_empty_pages_and_extracts_text(tmp_path):
    path = tmp_path / "test.pdf"
    path.write_bytes(b"%PDF-1.4\n")

    mock_page = MagicMock()
    mock_page.extract_text.return_value = ""
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]

    with patch("pypdf.PdfReader", return_value=mock_reader):
        with pytest.raises(IngestError, match="no text was found"):
            parse_pdf(path)


def test_parse_pdf_raises_on_page_extract_error(tmp_path):
    path = tmp_path / "test.pdf"
    path.write_bytes(b"%PDF-1.4\n")

    mock_page = MagicMock()
    mock_page.extract_text.side_effect = RuntimeError("extraction failed")
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]

    with patch("pypdf.PdfReader", return_value=mock_reader):
        with pytest.raises(IngestError, match="Could not extract text from PDF"):
            parse_pdf(path)


def test_parse_html_basic_text_extraction(tmp_path):
    html = "<html><head><title>Hello</title></head><body><p>World content</p></body></html>"
    path = _html_file(tmp_path, html)
    text, meta = parse_html(path)
    assert "World content" in text
    assert meta["format"] == "html"


def test_parse_html_raises_on_unicode_decode_error(tmp_path):
    path = tmp_path / "bad.html"
    path.write_bytes(b"\xff\xfe invalid utf8 bytes \x80\x81")
    with pytest.raises(IngestError, match="UTF-8"):
        parse_html(path)


def test_parse_html_raises_when_no_visible_text(tmp_path):
    html = "<html><head></head><body><script>alert(1);</script><style>body{color:red}</style></body></html>"
    path = _html_file(tmp_path, html)
    with pytest.raises(IngestError, match="visible text"):
        parse_html(path)


def test_parse_html_extracts_title_into_meta(tmp_path):
    html = "<html><head><title>Page Title</title></head><body><p>Content here.</p></body></html>"
    path = _html_file(tmp_path, html)
    text, meta = parse_html(path)
    assert meta.get("title") == "Page Title"
    assert "Page Title" in text


def test_parse_html_detects_canonical_url(tmp_path):
    html = (
        "<html><head>"
        '<link rel="canonical" href="https://example.com/page"/>'
        "</head><body><p>Content.</p></body></html>"
    )
    path = _html_file(tmp_path, html)
    _, meta = parse_html(path)
    assert meta.get("source_url") == "https://example.com/page"


def test_parse_html_detects_og_url_meta(tmp_path):
    html = (
        "<html><head>"
        '<meta property="og:url" content="https://example.com/og"/>'
        "</head><body><p>Content here.</p></body></html>"
    )
    path = _html_file(tmp_path, html)
    _, meta = parse_html(path)
    assert meta.get("source_url") == "https://example.com/og"


def test_parse_html_collects_links(tmp_path):
    html = (
        "<html><head><title>T</title></head><body>"
        "<p>See <a href='/other'>other page</a> for more.</p>"
        "</body></html>"
    )
    path = _html_file(tmp_path, html)
    _, meta = parse_html(path)
    assert "links" in meta
    assert any(lnk["href"] == "/other" for lnk in meta["links"])


def test_parse_html_base_href_resolves_links(tmp_path):
    html = (
        "<html><head><base href='https://base.example.com/'/></head>"
        "<body><a href='page.html'>link</a><p>text</p></body></html>"
    )
    path = _html_file(tmp_path, html)
    _, meta = parse_html(path)
    links = meta.get("links", [])
    assert any("base.example.com" in lnk["href"] for lnk in links)


def test_parse_html_skips_hidden_elements(tmp_path):
    html = "<html><body><div hidden>secret</div><p>visible</p></body></html>"
    path = _html_file(tmp_path, html)
    text, _ = parse_html(path)
    assert "secret" not in text
    assert "visible" in text


def test_parse_html_skips_display_none_elements(tmp_path):
    html = '<html><body><div style="display:none">hidden</div><p>shown</p></body></html>'
    path = _html_file(tmp_path, html)
    text, _ = parse_html(path)
    assert "hidden" not in text
    assert "shown" in text


def test_parse_html_handles_list_items(tmp_path):
    html = "<html><body><ul><li>item one</li><li>item two</li></ul></body></html>"
    path = _html_file(tmp_path, html)
    text, _ = parse_html(path)
    assert "item one" in text
    assert "item two" in text


def test_parse_html_render_text_without_title_element(tmp_path):
    # When there is no <title> element, render_text returns body directly.
    html = "<html><body><h1>Top Section</h1><p>content here</p></body></html>"
    path = _html_file(tmp_path, html)
    text, meta = parse_html(path)
    assert "Top Section" in text
    assert "title" not in meta


def test_parse_html_anchor_with_whitespace_only_href(tmp_path):
    # <a href="   "> has truthy raw href but strips to empty; should not produce a link.
    html = '<html><body><p><a href="   ">no link</a> text</p></body></html>'
    path = _html_file(tmp_path, html)
    _, meta = parse_html(path)
    assert not meta.get("links")


def test_is_hidden_detects_aria_hidden(tmp_path):
    assert _is_hidden({"aria-hidden": "true"}) is True
    assert _is_hidden({"aria-hidden": "false"}) is False


def test_is_hidden_detects_visibility_hidden(tmp_path):
    assert _is_hidden({"style": "visibility:hidden"}) is True
    assert _is_hidden({"style": "color:red"}) is False


def test_parse_html_nested_skip_tags(tmp_path):
    # Nested <div> inside skipped content should not emit text.
    html = "<html><body><nav><div>nav content</div></nav><p>real text</p></body></html>"
    path = _html_file(tmp_path, html)
    text, _ = parse_html(path)
    assert "nav content" not in text
    assert "real text" in text


def test_parse_html_non_canonical_link_tag_ignored(tmp_path):
    # A <link rel="stylesheet"> should not set source_url.
    html = (
        "<html><head>"
        '<link rel="stylesheet" href="styles.css"/>'
        '</head><body><p>Content.</p></body></html>'
    )
    path = _html_file(tmp_path, html)
    _, meta = parse_html(path)
    assert "source_url" not in meta


def test_parse_html_non_og_meta_tag_ignored(tmp_path):
    # <meta name="description"> should not set source_url.
    html = (
        "<html><head>"
        '<meta name="description" content="some description"/>'
        "</head><body><p>Content.</p></body></html>"
    )
    path = _html_file(tmp_path, html)
    _, meta = parse_html(path)
    assert "source_url" not in meta


def test_parse_html_whitespace_only_text_node_ignored(tmp_path):
    # Whitespace-only data nodes should collapse to empty and be skipped.
    html = "<html><body><p>word1</p>   <p>word2</p></body></html>"
    path = _html_file(tmp_path, html)
    text, _ = parse_html(path)
    assert "word1" in text
    assert "word2" in text
