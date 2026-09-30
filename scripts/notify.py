"""Shared email delivery for the job-search scans (Gmail SMTP over SSL)."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def send_email(subject: str, html: str, text: str, cfg: dict) -> None:
    """Send a multipart text+HTML email via Gmail SMTP (SSL). cfg: to, from, password."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(cfg["from"], cfg["password"])
        server.send_message(msg)


_REQUIRED = ("JOBSEARCH_EMAIL_TO", "JOBSEARCH_EMAIL_FROM", "GMAIL_APP_PASSWORD")


def email_cfg() -> dict:
    """Resolve to/from/password from env. All three are required -- there is no default
    address. In GitHub Actions the two addresses are repository VARIABLES and the
    password is a SECRET (Settings -> Secrets and variables -> Actions)."""
    missing = [v for v in _REQUIRED if not os.environ.get(v)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Set JOBSEARCH_EMAIL_TO / JOBSEARCH_EMAIL_FROM as repository variables and "
            "GMAIL_APP_PASSWORD as a repository secret.")
    return {"to": os.environ["JOBSEARCH_EMAIL_TO"],
            "from": os.environ["JOBSEARCH_EMAIL_FROM"],
            "password": os.environ["GMAIL_APP_PASSWORD"]}
