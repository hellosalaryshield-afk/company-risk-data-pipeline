"""Email a company scorecard PDF to someone who asked for it on the website.

Any SMTP server works, so it can run free: a Gmail app password (about 500 mails a day)
or Brevo's free plan (300 a day). Without `smtp_host`, the message is written to
reports/outbox as an .eml file, which any mail app opens, so the flow can be tried locally.
"""

import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from app.config.settings import Settings

OUTBOX = Path("reports/outbox")
SENDER_NAME = "Salary Shield"


class EmailNotSent(RuntimeError):
    pass


def scorecard_email(
    to_name: str, to_email: str, company_name: str, summary: str, pdf: bytes, settings: Settings
) -> EmailMessage:
    first_name = to_name.split()[0] if to_name.strip() else "there"
    message = EmailMessage()
    message["Subject"] = f"{company_name}: your Salary Shield risk scorecard"
    message["From"] = formataddr((SENDER_NAME, settings.report_from_email or "reports@salaryshield.local"))
    message["To"] = formataddr((to_name, to_email))
    if settings.report_reply_to:
        message["Reply-To"] = settings.report_reply_to
    message["Message-ID"] = make_msgid(domain="salaryshield")
    message.set_content(
        f"Hi {first_name},\n\n"
        f"Here is the full risk scorecard for {company_name}, attached as a PDF.\n\n"
        f"{summary}\n\n"
        "The score is indicative and built only from public data. It is not a prediction, "
        "and no score can promise a job is safe.\n\n"
        "We're building Salary Shield, India's first salary insurance: your full salary for up "
        "to 6 months if you're laid off. If you haven't yet, you can help shape it with our "
        "3-minute survey: https://salary-shield.netlify.app/#survey\n\n"
        "Salary Shield\n"
    )
    filename = company_name.lower().replace(" ", "_") + "_scorecard.pdf"
    message.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)
    return message


def deliver(message: EmailMessage, settings: Settings) -> str:
    """Send the message, or save it to the outbox when no mail server is configured.

    Returns "sent" or "saved"; raises EmailNotSent if the server refuses it.
    """
    if not settings.smtp_host:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        name = message["Message-ID"].strip("<>").split("@")[0] + ".eml"
        (OUTBOX / name).write_bytes(bytes(message))
        return "saved"

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password or "")
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailNotSent(f"{exc.__class__.__name__}: {exc}") from exc
    return "sent"
