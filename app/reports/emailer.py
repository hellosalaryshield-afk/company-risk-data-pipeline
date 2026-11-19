"""Email a company scorecard PDF to someone who asked for it on the website.

Three ways out, tried in this order:
- the Apps Script mail relay (`mail_relay_url`), which sends from Gmail over HTTPS and is
  what runs on Render, whose free plan blocks SMTP ports;
- any SMTP server (`smtp_host`), e.g. a Gmail app password, which works locally;
- neither: the message is written to reports/outbox as an .eml file.

The wording and look follow the template the team approved.
Each message carries a plain-text and an HTML version; mail apps show the HTML one.
"""

import base64
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
from html import escape
from pathlib import Path

import httpx

from app.config.settings import Settings

OUTBOX = Path("reports/outbox")
SENDER_NAME = "Salary Shield"
SURVEY_URL = "https://salary-shield.netlify.app/#survey"
LINKEDIN_URL = "https://www.linkedin.com/company/salary-shield/"
PHONE = "+91 9002473734"

POINTS = (
    "The score estimates how likely layoffs are over the next 3 to 6 months. It's a statistical "
    "estimate, not a prediction, and it says nothing about any individual role.",
    "Where data is thin, for example at unlisted companies, the report says so in its confidence rating.",
    "Use it alongside salary, role and team, not instead of them.",
)


class EmailNotSent(RuntimeError):
    pass


def plain_body(first_name: str, company_name: str) -> str:
    points = "\n".join(f"  {n}. {point}" for n, point in enumerate(POINTS, 1))
    return (
        f"Hi {first_name},\n\n"
        f"Your Layoff Risk Report for {company_name} is attached.\n\n"
        "A few things worth knowing:\n\n"
        f"{points}\n\n"
        "If something looks off, or you'd like to have a conversation, just reply to this email. "
        "We read every reply.\n\n"
        "We are building Salary Shield, a digital employee-protection platform tailored for India's "
        "white-collar salaried workforce. If you haven't yet, you can help shape it with our "
        f"3-minute survey: {SURVEY_URL}\n\n"
        "-- \n"
        "Team Salary Shield\n"
        "Building India's first Employee Protection platform\n"
        f"Follow us: {LINKEDIN_URL}\n"
        f"M | {PHONE}\n"
    )


def html_body(first_name: str, company_name: str) -> str:
    font = 'style="font-family:verdana,sans-serif"'
    points = "".join(f"<li>{escape(point)}</li>" for point in POINTS)
    return (
        f'<div {font}>'
        f"<p>Hi {escape(first_name)},</p>"
        f"<p>Your Layoff Risk Report for {escape(company_name)} is attached.</p>"
        f"<p>A few things worth knowing:</p><ol>{points}</ol>"
        "<p>If something looks off, or you&#39;d like to have a conversation, just reply to this email. "
        "We read every reply.</p>"
        "<p>We are building <b>Salary Shield</b>, a digital employee-protection platform tailored for "
        "India&#8217;s white-collar salaried workforce. If you haven&#39;t yet, you can help shape it with "
        f'our 3-minute survey: <a href="{SURVEY_URL}">{SURVEY_URL}</a></p>'
        "</div>"
        '<div style="font-family:monospace">'
        "<div>-- </div>"
        '<div><b style="color:#ff9900">Team Salary Shield</b></div>'
        '<div style="font-size:x-small"><i>Building India&#39;s first Employee Protection platform</i></div>'
        f'<div style="font-size:x-small"><i>Follow us: </i>'
        f'<a href="{LINKEDIN_URL}" style="color:#3d85c6">{LINKEDIN_URL}</a></div>'
        f'<div style="font-size:x-small">M | {PHONE}</div>'
        "</div>"
    )


def scorecard_email(to_name: str, to_email: str, company_name: str, pdf: bytes, settings: Settings) -> EmailMessage:
    first_name = to_name.split()[0] if to_name.strip() else "there"
    message = EmailMessage()
    message["Subject"] = f"Your Salary Shield report for {company_name} is ready"
    message["From"] = formataddr((SENDER_NAME, settings.report_from_email or "reports@salaryshield.local"))
    message["To"] = formataddr((to_name, to_email))
    if settings.report_reply_to:
        message["Reply-To"] = settings.report_reply_to
    message["Message-ID"] = make_msgid(domain="salaryshield")
    message.set_content(plain_body(first_name, company_name))
    message.add_alternative(html_body(first_name, company_name), subtype="html")
    filename = company_name.lower().replace(" ", "_") + "_scorecard.pdf"
    message.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)
    return message


def relay_payload(message: EmailMessage, settings: Settings) -> dict:
    """Turn the message into the JSON the Apps Script relay expects."""
    text = message.get_body(("plain",))
    html = message.get_body(("html",))
    payload = {
        "key": settings.mail_relay_key,
        "to": parseaddr(message["To"])[1],
        "subject": message["Subject"],
        "text": text.get_content() if text else "",
        "html": html.get_content() if html else "",
        "reply_to": message["Reply-To"] or "",
        "sender_name": SENDER_NAME,
    }
    attachment = next(message.iter_attachments(), None)
    if attachment is not None:
        payload["attachment"] = {
            "name": attachment.get_filename(),
            "mime_type": attachment.get_content_type(),
            "base64": base64.b64encode(attachment.get_payload(decode=True)).decode("ascii"),
        }
    return payload


def send_via_relay(message: EmailMessage, settings: Settings) -> None:
    try:
        # Apps Script answers a POST with a redirect to the result; httpx follows it with a GET.
        response = httpx.post(
            settings.mail_relay_url, json=relay_payload(message, settings), follow_redirects=True, timeout=60
        )
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise EmailNotSent(f"Mail relay unreachable: {exc.__class__.__name__}: {exc}") from exc
    if not result.get("ok"):
        raise EmailNotSent(f"Mail relay refused the message: {result.get('error', 'unknown error')}")


def deliver(message: EmailMessage, settings: Settings) -> str:
    """Send the message through the mail relay or SMTP, or save it to the outbox.

    The relay wins when configured; without a relay or `smtp_host` the message is saved
    as an .eml file. Returns "sent" or "saved"; raises EmailNotSent on failure.
    """
    if settings.mail_relay_url:
        send_via_relay(message, settings)
        return "sent"

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
