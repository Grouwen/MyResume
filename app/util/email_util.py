from email.message import EmailMessage
import aiosmtplib

from app.config.email_config import email_config


async def send_email(to_email:str,subject:str,html_content:str):
    msg = EmailMessage()
    msg["From"] = email_config.sender_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.add_alternative(html_content, subtype="html")

    await aiosmtplib.send(
        msg,
        hostname=email_config.host,
        port=465,
        username=email_config.sender_email,
        password=email_config.auth_code,
        use_tls=True,
    )