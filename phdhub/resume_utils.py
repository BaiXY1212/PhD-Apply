"""Resume parsing helpers based on PyMuPDF."""

import re
import io


def _normalize_text(text):
    text = text or ""
    text = re.sub(r"\u00a0", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text_from_pdf_bytes(pdf_bytes):
    if not pdf_bytes:
        return ""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return ""

    pages = []
    for page in doc:
        try:
            txt = page.get_text("text") or ""
        except Exception:
            txt = ""
        if txt.strip():
            pages.append(txt.strip())

    return _normalize_text("\n\n".join(pages))


def build_pdf_thumbnail_png(pdf_bytes=None, pdf_path="", width=260):
    """
    Return PNG bytes of first page thumbnail, or b"" when failed.
    """
    try:
        import fitz  # PyMuPDF
    except Exception:
        return b""

    try:
        if pdf_bytes:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        elif pdf_path:
            doc = fitz.open(pdf_path)
        else:
            return b""
        if len(doc) == 0:
            return b""
        page = doc[0]
        rect = page.rect
        zoom = max(width / max(rect.width, 1), 0.1)
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return pix.tobytes("png")
    except Exception:
        return b""
