# PDF and HTML Ingest Parsing Quirks

**Spec:** ../../specs/kb-librarian.md
**Last updated:** 2026-05-09

## What

PDF and local HTML ingest have specific scope limitations and failure modes that differ from markdown/text ingest.

## Why it matters

Parser failures that are silently dropped create gaps in the KB that are invisible until a user notices missing knowledge. The failure must be surfaced as a reviewable item.

### PDF

- **Local only.** No OCR, no image extraction. PDFs with image-only content (scanned documents) will produce empty or near-empty notes.
- **Parser failures** must surface as reviewable items in the review queue, not as silent drops. The failing file path and failure stage must be included.
- **Error log.** Failures that block ingest should write to `.kb/errors.log` with stage information so `kb doctor` can surface them.

### HTML

- **Local only.** Remote URLs are explicitly out of scope for Phase 4c. Only file-path arguments are accepted; URLs are rejected with a clear error.
- **No JavaScript rendering.** HTML is parsed statically; dynamic content loaded by JS is not captured.
- **Parser failures** follow the same pattern as PDF: reviewable item, not silent drop.

## See also

- [../../adrs/2026-05-09-robustness-patterns.md](../../adrs/2026-05-09-robustness-patterns.md)
