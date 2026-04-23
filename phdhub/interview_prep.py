"""Interview prep helpers: schedule parsing and Scholar paper retrieval."""

from datetime import datetime, time as dt_time
import re
import urllib.parse
import urllib.request


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def parse_interview_time(raw):
    if not raw:
        return None
    raw = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except Exception:
            continue
    return None


def get_interview_picker_defaults(raw):
    dt = parse_interview_time(raw)
    if dt:
        return dt.date(), dt.time().replace(second=0, microsecond=0)
    now = datetime.now()
    return now.date(), dt_time(hour=9, minute=0)


def format_interview_time(date_obj, time_obj):
    return f"{date_obj.strftime('%Y-%m-%d')} {time_obj.strftime('%H:%M')}"


def get_interview_records(db_rows):
    records = []
    for idx, row in enumerate(db_rows):
        if row.get("阶段") != "面试预约阶段":
            continue
        raw_time = row.get("面试时间", "")
        if not raw_time:
            continue
        parsed = parse_interview_time(raw_time)
        records.append(
            {
                "idx": idx,
                "row": row,
                "parsed_time": parsed,
                "raw_time": raw_time,
            }
        )
    records.sort(key=lambda x: (x["parsed_time"] is None, x["parsed_time"] or datetime.max))
    return records


def _fetch_html(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def get_homepage_text_excerpt(homepage_url, limit=3000):
    if not homepage_url:
        return ""
    try:
        html = _fetch_html(homepage_url, timeout=12)
        text = re.sub(r"<style.*?>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<script.*?>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit]
    except Exception:
        return ""


def _normalize_scholar_url(url):
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return f"https://scholar.google.com{url}"
    if "scholar.google.com" in url and not url.startswith("http"):
        return f"https://{url.lstrip('/')}"
    return url


def find_scholar_url_from_homepage(homepage_url):
    if not homepage_url:
        return "", "homepage_missing"
    try:
        html = _fetch_html(homepage_url, timeout=12)
        matches = re.findall(
            r'href=["\']([^"\']*scholar\.google\.[^"\']*citations[^"\']*)["\']',
            html,
            flags=re.IGNORECASE,
        )
        if matches:
            return _normalize_scholar_url(matches[0]), "homepage_link"
    except Exception:
        return "", "homepage_fetch_failed"
    return "", "not_found_on_homepage"


def search_scholar_profile(prof_name, univ_name=""):
    if not prof_name:
        return "", "name_missing"
    query = f"{prof_name} {univ_name}".strip()
    url = (
        "https://scholar.google.com/citations?hl=en&view_op=search_authors&mauthors="
        + urllib.parse.quote_plus(query)
    )
    try:
        html = _fetch_html(url, timeout=12)
        m = re.search(r'href=["\'](/citations\?user=[^"\']+)["\']', html)
        if m:
            return _normalize_scholar_url(m.group(1)), "search_authors"
    except Exception:
        return "", "search_failed"
    return "", "search_empty"


def fetch_recent_papers_from_scholar(profile_url, limit=10):
    if not profile_url:
        return [], "profile_missing"
    profile_url = _normalize_scholar_url(profile_url)
    sep = "&" if "?" in profile_url else "?"
    list_url = f"{profile_url}{sep}hl=en&view_op=list_works&sortby=pubdate"
    try:
        html = _fetch_html(list_url, timeout=15)
    except Exception:
        return [], "profile_fetch_failed"

    rows = re.findall(r'(<tr class="gsc_a_tr".*?</tr>)', html, flags=re.DOTALL)
    papers = []
    for row_html in rows:
        title_match = re.search(r'class="gsc_a_at"[^>]*>(.*?)</a>', row_html, flags=re.DOTALL)
        year_match = re.search(r'class="gsc_a_h gsc_a_hc gs_ibl">(\d{4})<', row_html)
        meta = re.findall(r'<div class="gs_gray">(.*?)</div>', row_html, flags=re.DOTALL)
        href_match = re.search(r'class="gsc_a_at" href="([^"]+)"', row_html)

        if not title_match:
            continue
        title = re.sub(r"\s+", " ", re.sub(r"<.*?>", "", title_match.group(1))).strip()
        year = year_match.group(1) if year_match else ""
        venue = re.sub(r"\s+", " ", re.sub(r"<.*?>", "", meta[1] if len(meta) > 1 else "")).strip()
        paper_url = _normalize_scholar_url(href_match.group(1)) if href_match else ""

        papers.append(
            {
                "title": title,
                "year": year,
                "venue": venue,
                "url": paper_url,
            }
        )
        if len(papers) >= limit:
            break

    return papers, "ok" if papers else "papers_empty"


def resolve_recent_papers(prof_name, univ_name="", homepage_url="", preset_scholar_url="", limit=10):
    scholar_url = _normalize_scholar_url(preset_scholar_url)
    source = "preset"

    if not scholar_url:
        scholar_url, source = find_scholar_url_from_homepage(homepage_url)
    if not scholar_url:
        scholar_url, source = search_scholar_profile(prof_name, univ_name)
    if not scholar_url:
        return {"scholar_url": "", "papers": [], "source": source, "status": "scholar_not_found"}

    papers, status = fetch_recent_papers_from_scholar(scholar_url, limit=limit)
    return {
        "scholar_url": scholar_url,
        "papers": papers,
        "source": source,
        "status": status,
    }
