"""SMTP delivery for authentication and organization emails."""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr, formataddr


class EmailDeliveryError(RuntimeError):
    """Raised when email delivery is not configured or fails."""


def send_email(*, recipient: str, subject: str, text: str, message_id: str | None = None) -> str | None:
    if os.getenv("BLOODNET_EMAIL_TEST_MODE", "false").lower() == "true":
        allowed = {value.strip().casefold() for value in os.getenv("BLOODNET_EMAIL_TEST_RECIPIENTS", "").split(",") if value.strip()}
        if recipient.strip().casefold() not in allowed:
            raise EmailDeliveryError("Recipient is outside the configured test delivery scope")
        subject = "[TEST ONLY] " + subject
        text = ("This is a BloodNet validation exercise. No real donation or attendance is requested. "
                "The request, blood units and receipts belong to the isolated validation database.\n\n" + text)
    host = os.getenv("BLOODNET_SMTP_HOST")
    username = os.getenv("BLOODNET_SMTP_USERNAME")
    password = os.getenv("BLOODNET_SMTP_PASSWORD")
    sender = os.getenv("BLOODNET_EMAIL_FROM")
    if not all((host, username, password, sender)):
        if os.getenv("BLOODNET_ENV", "development").lower() in {"production", "prod"}:
            raise EmailDeliveryError("SMTP email delivery is not configured")
        # Never log reset links, verification tokens, or recipient addresses.
        return

    port = int(os.getenv("BLOODNET_SMTP_PORT", "587"))
    message = EmailMessage()
    _, sender_address = parseaddr(sender)
    message["From"] = formataddr((os.getenv("BLOODNET_EMAIL_SENDER_NAME", "BloodNet"), sender_address))
    message["To"] = recipient
    message["Subject"] = subject
    message["Message-ID"] = message_id or make_msgid()
    message.set_content(text)
    try:
        context = ssl.create_default_context()
        transport = smtplib.SMTP_SSL(host, port, timeout=15, context=context) if port == 465 else smtplib.SMTP(host, port, timeout=15)
        with transport as connection:
            if port != 465:
                connection.starttls(context=context)
            connection.login(username, password)
            refused = connection.send_message(message)
            if refused:
                raise EmailDeliveryError("SMTP recipient was refused")
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailDeliveryError("SMTP email delivery failed") from exc
    return str(message["Message-ID"])
