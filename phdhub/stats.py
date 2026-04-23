"""Analytics helpers for dashboard metrics."""

from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime


def get_email_stats_from_emails(emails, recent_days=None):
    stats = {
        "sent_inquiry": 0,
        "replied_total": 0,
        "positive_reply": 0,
        "negative_reply": 0,
        "neutral_reply": 0,
    }

    if not emails:
        return stats

    today = datetime.now().date()
    window_start = None
    if isinstance(recent_days, int) and recent_days > 0:
        window_start = today - timedelta(days=recent_days - 1)

    for em in emails:
        if not em.get("is_phd_related"):
            continue

        cat = em.get("phd_category")
        if cat not in [1, 2, 3, 4]:
            continue

        if window_start is not None:
            raw_date = em.get("date", "")
            try:
                dt = parsedate_to_datetime(raw_date)
                if dt is None:
                    continue
                if dt.tzinfo is not None:
                    dt = dt.astimezone()
                mail_date = dt.date()
            except Exception:
                continue
            if not (window_start <= mail_date <= today):
                continue

        if cat == 1:
            stats["sent_inquiry"] += 1
        elif cat == 2:
            stats["positive_reply"] += 1
            stats["replied_total"] += 1
        elif cat == 3:
            stats["negative_reply"] += 1
            stats["replied_total"] += 1
        elif cat == 4:
            stats["neutral_reply"] += 1
            stats["replied_total"] += 1

    return stats


def get_recent_7d_email_stats_from_emails(emails):
    return get_email_stats_from_emails(emails, recent_days=7)
