"""Email parsing and IMAP client helpers."""

import email
import imaplib
from email.header import decode_header


def decode_mime_words(s):
    if not s:
        return ""
    try:
        decoded_words = decode_header(s)
        text = ""
        for word, encoding in decoded_words:
            if isinstance(word, bytes):
                text += word.decode(encoding if encoding else "utf-8", errors="ignore")
            else:
                text += str(word)
        return text
    except Exception:
        return str(s)


def get_email_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdispo = str(part.get("Content-Disposition"))
            if ctype == "text/plain" and "attachment" not in cdispo:
                try:
                    body += part.get_payload(decode=True).decode("utf-8", errors="ignore")
                except Exception:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
        except Exception:
            pass
    return body


def test_imap_connection(email_add, password, imap_server):
    try:
        mail = imaplib.IMAP4_SSL(imap_server, 993)
        mail.login(email_add, password)
        status, _ = mail.select("inbox")
        if status != "OK":
            return False, "无法选择收件箱 (Inbox)"

        res, data = mail.search(None, "ALL")
        if res != "OK":
            return False, "无法搜索邮件"

        mail_ids = data[0].split()
        latest_email_ids = mail_ids[-5:]
        latest_email_ids.reverse()

        recent_emails = []
        for e_id in latest_email_ids:
            res, msg_data = mail.fetch(e_id, "(RFC822)")
            if res != "OK":
                continue
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject = decode_mime_words(msg["Subject"])
                    from_ = decode_mime_words(msg.get("From", ""))
                    date_ = decode_mime_words(msg.get("Date", ""))
                    short_subject = subject[:80] + "..." if len(subject) > 80 else subject
                    recent_emails.append({"发件人": from_, "主题": short_subject, "时间": date_})

        mail.close()
        mail.logout()
        return True, recent_emails
    except Exception as e:
        return False, str(e)


def fetch_all_emails(email_add, password, imap_server, limit=15):
    try:
        mail = imaplib.IMAP4_SSL(imap_server, 993)
        mail.login(email_add, password)
        mail.select("inbox")
        res, data = mail.search(None, "ALL")
        if res != "OK":
            return False, "无法搜索邮件"

        mail_ids = data[0].split()
        latest_ids = mail_ids[-limit:]
        latest_ids.reverse()
        emails = []
        for e_id in latest_ids:
            res, msg_data = mail.fetch(e_id, "(RFC822)")
            if res != "OK":
                continue
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject = decode_mime_words(msg["Subject"])
                    from_ = decode_mime_words(msg.get("From", ""))
                    to_ = decode_mime_words(msg.get("To", ""))
                    date_ = decode_mime_words(msg.get("Date", ""))
                    body = get_email_body(msg)
                    emails.append(
                        {
                            "id": e_id.decode(),
                            "subject": subject,
                            "from": from_,
                            "to": to_,
                            "date": date_,
                            "body": body,
                        }
                    )
        mail.close()
        mail.logout()
        return True, emails
    except Exception as e:
        return False, str(e)

