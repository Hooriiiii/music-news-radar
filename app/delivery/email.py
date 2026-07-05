import smtplib
from email.message import EmailMessage

from app.config import settings


def send_email(subject: str, html: str, text: str) -> None:
    missing = [
        name
        for name, value in (
            ("SMTP_HOST", settings.smtp_host),
            ("SMTP_USER", settings.smtp_user),
            ("SMTP_PASSWORD", settings.smtp_password),
            ("DIGEST_TO", settings.digest_to),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Config SMTP incomplète dans .env : {', '.join(missing)}")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.digest_from or settings.smtp_user
    message["To"] = settings.digest_to
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
