"""Generic SMTP email sender for the auth flows.

Sends the sign-up verification code, password-reset link, team-invite link and
workspace-request notifications. When SMTP isn't configured (no SMTP_HOST /
from-address) every send is a no-op returning False, so local dev keeps working
via the dev-code/link fallback. All sends are best-effort: a failure is logged
and returns False, never raises, so it can't break registration / reset / invite.
"""
import html
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import get_settings

settings = get_settings()
log = logging.getLogger("uvicorn.error")

BRAND = "#F58220"
BRAND_HOVER = "#EA6C18"
FG = "#211f1c"
FG2 = "#6f6a60"
FG3 = "#9a948a"
BORDER = "#ECE7DD"
BG = "#f6f5ef"
CARD = "#ffffff"
CHAMPAGNE = "#FFF8F0"


def is_configured() -> bool:
    return settings.smtp_configured


def send_email(to: str, subject: str, html_body: str, text: str | None = None) -> bool:
    if not is_configured():
        return False
    msg = EmailMessage()
    msg["From"] = f"{settings.mail_from_name} <{settings.mail_sender}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text or "This message needs an HTML-capable email client.")
    msg.add_alternative(html_body, subtype="html")
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
# Templates (table-based HTML for Gmail / Outlook / SES)
# --------------------------------------------------------------------------- #
def _esc(v: str | None) -> str:
    return html.escape(v or "", quote=True)


def _shell(title: str, body: str, *, footer: str | None = None, eyebrow: str | None = None) -> str:
    """Branded email chrome: champagne page, white card, orange accent bar."""
    foot = footer if footer is not None else (
        "If you didn&rsquo;t request this, you can ignore this email."
    )
    eye = (
        f'<p style="margin:0 0 8px;font-size:11px;font-weight:700;letter-spacing:.14em;'
        f'text-transform:uppercase;color:{BRAND}">{_esc(eyebrow)}</p>'
        if eyebrow else ""
    )
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title></head>
<body style="margin:0;padding:0;background:{BG};-webkit-font-smoothing:antialiased;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:{BG};padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0"
             style="width:100%;max-width:560px;background:{CARD};border-radius:16px;
                    border:1px solid {BORDER};overflow:hidden;
                    box-shadow:0 12px 40px rgba(120,90,60,0.08);">
        <!-- accent bar -->
        <tr><td style="height:4px;background:linear-gradient(90deg,{BRAND},{BRAND_HOVER});
                       font-size:0;line-height:0;">&nbsp;</td></tr>
        <!-- brand -->
        <tr><td style="padding:28px 32px 0;font-family:Segoe UI,Helvetica,Arial,sans-serif;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td style="width:36px;height:36px;border-radius:10px;
                         background:linear-gradient(135deg,#F89438,#EA4F0A);
                         text-align:center;vertical-align:middle;">
                <span style="display:inline-block;width:36px;line-height:36px;
                             font-size:16px;font-weight:800;color:#fff;">P</span>
              </td>
              <td style="padding-left:12px;vertical-align:middle;">
                <div style="font-size:18px;font-weight:800;color:{FG};letter-spacing:-0.02em;
                            line-height:1.1;">Preduit</div>
                <div style="font-size:10px;font-weight:700;letter-spacing:0.16em;
                            text-transform:uppercase;color:{FG3};line-height:1.2;">
                  Retail ERP</div>
              </td>
            </tr>
          </table>
        </td></tr>
        <!-- title + body -->
        <tr><td style="padding:28px 32px 8px;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:{FG};">
          {eye}
          <h1 style="margin:0 0 16px;font-size:24px;font-weight:800;line-height:1.2;
                     letter-spacing:-0.02em;color:{FG};">{_esc(title)}</h1>
          {body}
        </td></tr>
        <!-- footer -->
        <tr><td style="padding:8px 32px 28px;font-family:Segoe UI,Helvetica,Arial,sans-serif;">
          <div style="border-top:1px solid {BORDER};padding-top:18px;
                      font-size:12px;line-height:1.55;color:{FG3};">{foot}</div>
        </td></tr>
      </table>
      <p style="margin:18px 0 0;font-family:Segoe UI,Helvetica,Arial,sans-serif;
                font-size:11px;color:{FG3};">
        &copy; Preduit &middot; Apparel &amp; retail ERP
      </p>
    </td></tr>
  </table>
</body>
</html>"""


def _button(label: str, url: str) -> str:
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:20px 0 8px;">'
        f'<tr><td style="border-radius:10px;background:{BRAND};">'
        f'<a href="{_esc(url)}" style="display:inline-block;padding:14px 24px;font-family:Segoe UI,Helvetica,Arial,sans-serif;'
        f'font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:10px;">'
        f'{_esc(label)}</a></td></tr></table>'
    )


def _detail_rows(pairs: list[tuple[str, str]]) -> str:
    rows = []
    for i, (k, v) in enumerate(pairs):
        border = f"border-bottom:1px solid {BORDER};" if i < len(pairs) - 1 else ""
        rows.append(
            f'<tr>'
            f'<td style="padding:12px 0;{border}width:120px;vertical-align:top;'
            f'font-size:12px;font-weight:700;letter-spacing:0.04em;text-transform:uppercase;'
            f'color:{FG3};">{_esc(k)}</td>'
            f'<td style="padding:12px 0;{border}font-size:15px;font-weight:600;color:{FG};'
            f'word-break:break-word;">{v}</td>'
            f'</tr>'
        )
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'
        f' style="margin:8px 0 20px;background:{CHAMPAGNE};border:1px solid {BORDER};'
        f'border-radius:12px;padding:4px 18px;">'
        f'<tr><td style="padding:4px 18px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'{"".join(rows)}</table></td></tr></table>'
    )


def send_verification_code(to: str, code: str) -> bool:
    body = (
        f'<p style="margin:0 0 8px;font-size:15px;line-height:1.6;color:{FG2};">'
        f'Enter this code to verify your email and secure your workspace. '
        f'It expires in <b style="color:{FG};">15 minutes</b>.</p>'
        f'<div style="margin:20px 0;padding:18px 20px;text-align:center;'
        f'background:{CHAMPAGNE};border:1px solid {BORDER};border-radius:12px;">'
        f'<div style="font-size:32px;font-weight:800;letter-spacing:10px;color:{FG};'
        f'font-family:Segoe UI,Helvetica,Arial,sans-serif;">{_esc(code)}</div></div>'
    )
    return send_email(
        to, "Your Preduit verification code",
        _shell("Verify your email", body, eyebrow="Security"),
        f"Your Preduit verification code is {code}. It expires in 15 minutes.",
    )


def send_password_reset(to: str, link: str) -> bool:
    body = (
        f'<p style="margin:0;font-size:15px;line-height:1.6;color:{FG2};">'
        f'We received a request to reset your password. This link works once and '
        f'expires in <b style="color:{FG};">30 minutes</b>.</p>'
        f'{_button("Reset my password", link)}'
        f'<p style="margin:8px 0 0;font-size:12px;line-height:1.55;color:{FG3};word-break:break-all;">'
        f'Or paste this link:<br>{_esc(link)}</p>'
    )
    return send_email(
        to, "Reset your Preduit password",
        _shell("Reset your password", body, eyebrow="Account"),
        f"Reset your Preduit password (expires in 30 minutes): {link}",
    )


def send_workspace_request(name: str, contact_number: str, email: str,
                           business_name: str, business_description: str) -> bool:
    email_cell = (
        f'<a href="mailto:{_esc(email)}" style="color:{BRAND};font-weight:700;'
        f'text-decoration:none;">{_esc(email)}</a>'
    )
    body = (
        f'<p style="margin:0 0 4px;font-size:15px;line-height:1.6;color:{FG2};">'
        f'Someone submitted a workspace request from the Preduit signup form. '
        f'Review the details below and set up their workspace when ready.</p>'
        f'{_detail_rows([
            ("Name", _esc(name)),
            ("Contact", _esc(contact_number)),
            ("Email", email_cell),
            ("Business", _esc(business_name)),
            ("Description", _esc(business_description) or "—"),
        ])}'
        f'{_button("Reply to requester", f"mailto:{email}")}'
        f'<p style="margin:4px 0 0;font-size:13px;line-height:1.55;color:{FG2};">'
        f'Reach them at <b style="color:{FG};">{_esc(email)}</b> once the workspace is ready.</p>'
    )
    plain = (
        f"New workspace request\n\nName: {name}\nContact: {contact_number}\n"
        f"Email: {email}\nBusiness: {business_name}\nDescription: {business_description or '—'}"
    )
    return send_email(
        "airotixpersonal@gmail.com",
        f"Workspace request from {name} — {business_name}",
        _shell(
            "New workspace request",
            body,
            eyebrow="Inbound lead",
            footer="This notification was sent by Preduit to your ops inbox. "
                   "It is not a customer-facing message.",
        ),
        plain,
    )


def send_invitation(to: str, company: str | None, role: str, link: str) -> bool:
    where = f" to <b style=\"color:{FG};\">{_esc(company)}</b>" if company else ""
    body = (
        f'<p style="margin:0;font-size:15px;line-height:1.6;color:{FG2};">'
        f'You&rsquo;ve been invited{where} as '
        f'<b style="color:{FG};">{_esc(role)}</b>. Accept the invite to set your password '
        f'and join the team.</p>'
        f'{_button("Accept invitation", link)}'
        f'<p style="margin:8px 0 0;font-size:12px;line-height:1.55;color:{FG3};word-break:break-all;">'
        f'Or paste this link:<br>{_esc(link)}</p>'
    )
    return send_email(
        to, f"You're invited to {company or 'a Preduit workspace'}",
        _shell("Join the team on Preduit", body, eyebrow="Invitation"),
        f"You've been invited as {role}. Accept here: {link}",
    )
