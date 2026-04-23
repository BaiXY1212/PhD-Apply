"""Background email sync and cache access."""

import email
import imaplib
import json
import os
import threading
import time

from .ai_services import classify_phd_email, extract_category
from .constants import EMAILS_CACHE_FILE
from .email_client import decode_mime_words, get_email_body
from .storage import load_config


def fetch_once():
    try:
        config = load_config()
        if config.get("email") and config.get("password") and config.get("imap_server"):
            # Fetch top 100 to satisfy any UI limit
            mail = imaplib.IMAP4_SSL(config["imap_server"], 993)
            mail.login(config["email"], config["password"])
            mail.select("inbox")
            res, data = mail.search(None, "ALL")
            mail_ids = data[0].split()
            latest_ids = mail_ids[-100:]
            latest_ids.reverse()
            existing_phd_tags = {}
            existing_phd_reasoning = {}
            existing_phd_category = {}
            existing_phd_details = {}
            if os.path.exists(EMAILS_CACHE_FILE):
                try:
                    with open(EMAILS_CACHE_FILE, "r", encoding="utf-8") as f:
                        old_data = json.load(f)
                        for em in old_data.get("emails", []):
                            if "is_phd_related" in em:
                                existing_phd_tags[em["id"]] = em["is_phd_related"]
                            if "phd_reasoning" in em:
                                existing_phd_reasoning[em["id"]] = em["phd_reasoning"]
                            if "phd_category" in em:
                                existing_phd_category[em["id"]] = em["phd_category"]
                            if "phd_details" in em:
                                existing_phd_details[em["id"]] = em["phd_details"]
                except Exception:
                    pass

            emails = []
            # We don't want to process 100 emails in one go for LLM, so limit processing
            processed_count = 0
            for e_id in latest_ids:
                try:
                    res, msg_data = mail.fetch(e_id, '(RFC822)')
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            subject = decode_mime_words(msg["Subject"])
                            from_ = decode_mime_words(msg.get("From", ""))
                            to_ = decode_mime_words(msg.get("To", ""))
                            date_ = decode_mime_words(msg.get("Date", ""))
                            body = get_email_body(msg)
                            mail_id_str = e_id.decode()
                            
                            is_phd = existing_phd_tags.get(mail_id_str)
                            reasoning = existing_phd_reasoning.get(mail_id_str, "")
                            phd_category = existing_phd_category.get(mail_id_str)
                            phd_details = existing_phd_details.get(mail_id_str, {})
                            
                            if is_phd is None and processed_count < 10:
                                is_phd, reasoning = classify_phd_email(subject, body, config)
                                if is_phd:
                                    phd_category = extract_category(reasoning)
                                processed_count += 1
                                time.sleep(1) # avoid rate limits
                                
                            emails.append({
                                "id": mail_id_str, "subject": subject, "from": from_, "to": to_, "date": date_, "body": body, 
                                "is_phd_related": is_phd, "phd_reasoning": reasoning, "phd_category": phd_category, "phd_details": phd_details
                            })
                except Exception:
                    continue
            mail.close()
            mail.logout()
            
            with open(EMAILS_CACHE_FILE, "w", encoding="utf-8") as cache_f:
                json.dump({"success": True, "emails": emails, "last_updated": time.time()}, cache_f, ensure_ascii=False)
    except Exception as e:
        with open(EMAILS_CACHE_FILE, "w", encoding="utf-8") as cache_f:
            json.dump({"success": False, "error": str(e), "last_updated": time.time()}, cache_f, ensure_ascii=False)

def fetch_and_cache_emails():
    while True:
        fetch_once()
        # 15 minutes sleep
        time.sleep(900)

def start_background_email_fetch():
    thread = threading.Thread(target=fetch_and_cache_emails, daemon=True)
    thread.start()
    return thread

def get_cached_emails(limit=15):
    if os.path.exists(EMAILS_CACHE_FILE):
        try:
            with open(EMAILS_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("success"):
                    return True, data.get("emails", [])[:limit]
                else:
                    return False, data.get("error", "Unknown Error")
        except:
            pass
    return True, [] # Return empty if not yet fetched
