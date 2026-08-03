"""Generic SMTP email sender for the auth flows.

Sends the sign-up verification code, password-reset link and team-invite link.
When SMTP isn't configured (no SMTP_HOST / from-address) every send is a no-op
returning False, so local dev keeps working via the dev-code/link fallback.
All sends are best-effort: a failure is logged and returns False, never raises,
so it can't break registration / reset / invite requests.
"""
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import get_settings

settings = get_settings()
log = logging.getLogger("uvicorn.error")

BRAND = "#F58220"


def is_configured() -> bool:
    return settings.smtp_configured


def send_email(to: str, subject: str, html: str, text: str | None = None) -> bool:
    if not is_configured():
        return False
    msg = EmailMessage()
    msg["From"] = f"{settings.mail_from_name} <{settings.mail_sender}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text or "This message needs an HTML-capable email client.")
    msg.add_alternative(html, subtype="html")
    try:
        if settings.smtp_port == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=ctx, timeout=15) as s:
                if settings.smtp_user:
                    s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
                if settings.smtp_use_tls:
                    s.starttls(context=ssl.create_default_context())
                if settings.smtp_user:
                    s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001 — never let email break the request
        log.warning("Email send to %s failed: %s: %s", to, type(exc).__name__, exc)
        return False


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
def _shell(title: str, body: str) -> str:
    return f"""\
<div style="font-family:Segoe UI,Arial,sans-serif;max-width:520px;margin:0 auto;padding:28px;
            color:#26241f;background:#ffffff">
  <div style="font-size:18px;font-weight:800;color:{BRAND};margin-bottom:18px">Preduit <span style="color:#a39c8f;font-size:11px;letter-spacing:.16em">RETAIL ERP</span></div>
  <h1 style="font-size:22px;margin:0 0 12px">{title}</h1>
  {body}
  <p style="margin-top:26px;font-size:12px;color:#9a948a">If you didn't request this, you can ignore this email.</p>
</div>"""


def _button(label: str, url: str) -> str:
    return (f'<a href="{url}" style="display:inline-block;background:{BRAND};color:#fff;'
            f'text-decoration:none;font-weight:700;padding:12px 22px;border-radius:10px;'
            f'margin:16px 0">{label}</a>')


def send_verification_code(to: str, code: str) -> bool:
    body = (f'<p style="font-size:15px;line-height:1.6;color:#6f6a60">Enter this code to verify your email and '
            f'secure your workspace. It expires in 15 minutes.</p>'
            f'<div style="font-size:34px;font-weight:800;letter-spacing:8px;margin:18px 0;color:#211f1c">{code}</div>')
    return send_email(to, "Your Preduit verification code",
                      _shell("Verify your email", body),
                      f"Your Preduit verification code is {code}. It expires in 15 minutes.")


def send_password_reset(to: str, link: str) -> bool:
    body = (f'<p style="font-size:15px;line-height:1.6;color:#6f6a60">We received a request to reset your password. '
            f'This link works once and expires in 30 minutes.</p>{_button("Reset my password", link)}'
            f'<p style="font-size:12px;color:#9a948a;word-break:break-all">Or paste this link: {link}</p>')
    return send_email(to, "Reset your Preduit password",
                      _shell("Reset your password", body),
                      f"Reset your Preduit password (expires in 30 minutes): {link}")


def send_invitation(to: str, company: str | None, role: str, link: str) -> bool:
    where = f" to <b>{company}</b>" if company else ""
    body = (f'<p style="font-size:15px;line-height:1.6;color:#6f6a60">You&rsquo;ve been invited{where} as '
            f'<b>{role}</b>. Accept the invite to set your password and join the team.</p>'
            f'{_button("Accept invitation", link)}'
            f'<p style="font-size:12px;color:#9a948a;word-break:break-all">Or paste this link: {link}</p>')
    return send_email(to, f"You're invited to {company or 'a Preduit workspace'}",
                      _shell("Join the team on Preduit", body),
                      f"You've been invited as {role}. Accept here: {link}")
