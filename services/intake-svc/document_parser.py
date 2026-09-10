"""Safe in-memory extraction for supported intake documents."""

from io import BytesIO

MAX_DOCUMENT_BYTES = 1 * 1024 * 1024
MAX_DOCUMENT_PAGES = 25
MAX_EXTRACTED_CHARS = 100_000
SUPPORTED_CONTENT_TYPES = {"application/pdf", "text/plain"}


def extract_document_text(content: bytes, content_type: str) -> str:
    """Extract text without persisting the uploaded document."""
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("Document exceeds the 1 MB intake limit")
    if content_type not in SUPPORTED_CONTENT_TYPES:
        raise ValueError("Only PDF and plain-text documents are supported")
    if content_type == "text/plain":
        text = content.decode("utf-8", errors="strict")
    else:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF intake requires the pypdf package") from exc
        reader = PdfReader(BytesIO(content), strict=False)
        if len(reader.pages) > MAX_DOCUMENT_PAGES:
            raise ValueError(f"PDF exceeds the {MAX_DOCUMENT_PAGES}-page intake limit")
        pages: list[str] = []
        extracted_chars = 0
        for page in reader.pages:
            page_text = page.extract_text() or ""
            extracted_chars += len(page_text)
            if extracted_chars > MAX_EXTRACTED_CHARS:
                raise ValueError("Extracted document text exceeds the 100,000-character limit")
            pages.append(page_text)
        text = "\n".join(pages)
    if len(text) > MAX_EXTRACTED_CHARS:
        raise ValueError("Extracted document text exceeds the 100,000-character limit")
    if not text.strip():
        raise ValueError("No readable text was found in the document")
    return text.strip()
