"""Shared file paths and defaults for local persistence."""

CONFIG_FILE = "phdhub_config.json"
DB_FILE = "phdhub_db.json"
EMAILS_CACHE_FILE = "phdhub_emails_cache.json"

DEFAULT_CONFIG = {
    "email": "",
    "password": "",
    "imap_server": "imap.gmail.com",
    "smtp_server": "smtp.gmail.com",
}

RESUME_DIR = "resumes"
RESUME_INDEX_FILE = "phdhub_resumes.json"

RP_DIR = "rps"
RP_INDEX_FILE = "phdhub_rps.json"
