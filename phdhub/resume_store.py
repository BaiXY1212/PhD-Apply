"""Resume storage and retrieval helpers (multi-file)."""

import json
import os
from datetime import datetime
from uuid import uuid4

from .constants import RESUME_DIR, RESUME_INDEX_FILE
from .resume_utils import extract_text_from_pdf_bytes


def _ensure_resume_dir():
    os.makedirs(RESUME_DIR, exist_ok=True)


def _load_index():
    if os.path.exists(RESUME_INDEX_FILE):
        with open(RESUME_INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    return []


def _save_index(items):
    with open(RESUME_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def list_resumes():
    items = _load_index()
    items.sort(key=lambda x: x.get("uploaded_at", ""), reverse=True)
    return items


def add_resume(filename, pdf_bytes):
    _ensure_resume_dir()
    resume_text = extract_text_from_pdf_bytes(pdf_bytes)
    if not resume_text:
        return (
            False,
            None,
            "无法解析PDF文本（可能是扫描件/图片PDF或受保护PDF）。建议先用OCR导出可复制文本后再上传。",
        )

    rid = str(uuid4())
    safe_name = f"{rid}.pdf"
    path = os.path.join(RESUME_DIR, safe_name)
    with open(path, "wb") as f:
        f.write(pdf_bytes)

    rec = {
        "id": rid,
        "filename": filename,
        "path": path,
        "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "text": resume_text,
    }
    items = _load_index()
    items.append(rec)
    _save_index(items)
    return True, rec, ""


def get_resume(resume_id):
    for r in _load_index():
        if r.get("id") == resume_id:
            return r
    return None


def delete_resume(resume_id):
    items = _load_index()
    target = None
    kept = []
    for r in items:
        if r.get("id") == resume_id:
            target = r
        else:
            kept.append(r)
    if not target:
        return False

    path = target.get("path", "")
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass
    _save_index(kept)
    return True
