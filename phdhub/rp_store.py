"""Research Proposal storage and retrieval helpers."""

import json
import os
from datetime import datetime
from uuid import uuid4

from .constants import RP_DIR, RP_INDEX_FILE
from .resume_utils import extract_text_from_pdf_bytes


def _ensure_rp_dir():
    os.makedirs(RP_DIR, exist_ok=True)


def _load_index():
    if os.path.exists(RP_INDEX_FILE):
        with open(RP_INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    return []


def _save_index(items):
    with open(RP_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def list_rps():
    items = _load_index()
    items.sort(key=lambda x: x.get("uploaded_at", ""), reverse=True)
    return items


def add_rp(filename, pdf_bytes):
    _ensure_rp_dir()
    rp_text = extract_text_from_pdf_bytes(pdf_bytes)
    if not rp_text:
        return False, None, "无法解析PDF文本（可能是扫描件或受保护PDF）"

    rid = str(uuid4())
    safe_name = f"{rid}.pdf"
    path = os.path.join(RP_DIR, safe_name)
    with open(path, "wb") as f:
        f.write(pdf_bytes)

    rec = {
        "id": rid,
        "filename": filename,
        "path": path,
        "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "text": rp_text,
    }
    items = _load_index()
    items.append(rec)
    _save_index(items)
    return True, rec, ""


def get_rp(rp_id):
    for r in _load_index():
        if r.get("id") == rp_id:
            return r
    return None


def delete_rp(rp_id):
    items = _load_index()
    target = None
    kept = []
    for r in items:
        if r.get("id") == rp_id:
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

