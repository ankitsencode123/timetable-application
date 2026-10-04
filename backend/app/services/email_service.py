"""Email service for notifying teachers of routine changes via Brevo."""
import os
import httpx
from typing import List, Dict, Any

from loguru import logger
from sqlalchemy.orm import Session
from app.actions.engine import EngineResult

# ============================================================
# BREVO CONFIGURATION
# Set BREVO_API_KEY in your .env or Render dashboard.
# ============================================================
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "ankitcursor478@gmail.com")
SENDER_NAME = os.getenv("SENDER_NAME", "Timetable Administrator")

# Hardcoded test recipients as requested by the user
TEST_RECIPIENTS = [
    {"email": "ankitsen53806626@gmail.com", "name": "Ankit"},
    {"email": "timetableadmin71@gmail.com", "name": "Timetable Admin"}
]


async def send_email(subject: str, html_body: str, recipients: List[Dict[str, str]]):
    """Send an email to a list of recipients asynchronously using Brevo HTTP API."""
    if not recipients:
        return
    if not BREVO_API_KEY:
        logger.warning("BREVO_API_KEY is not set. Email not sent.")
        return

    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json",
    }
    
    payload = {
        "sender": {
            "name": SENDER_NAME,
            "email": SENDER_EMAIL,
        },
        "to": recipients,
        "subject": subject,
        "htmlContent": html_body,
        "tags": ["timetable-change"]
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(BREVO_API_URL, headers=headers, json=payload)
            if response.is_success:
                logger.info(f"Notification email sent successfully. MsgID: {response.json().get('messageId')}")
            else:
                logger.error(f"Failed to send email. HTTP {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"Network error while sending email: {e}")


def _extract_teachers_from_entry(entry: Dict[str, Any]) -> List[str]:
    """Extract teacher short names from a timetable entry dict."""
    if not entry:
        return []
    teacher_field = entry.get("teacher", "")
    if not teacher_field:
        return []
    # In case there are multiple teachers in a single entry (e.g. practicals "A,B")
    return [t.strip() for t in teacher_field.split(",") if t.strip()]


async def notify_teachers_of_changes(result: EngineResult, db: Session):
    """
    Look through the EngineResult for applied actions, determine affected teachers,
    and send them an email notification via Brevo.
    """
    if not result.success and not result.partial_applied:
        return  # No changes applied

    # Usually, we'd find the affected_short_names, resolve them to user emails via db, 
    # and send directly. For this requested integration, we send to exactly two emails.
    
    subject = "Notification: Routine Change in Timetable System"
    change_log_str = result.change_log if result.change_log is not None else "Unknown details"
    
    # Format the change log to HTML replacing newlines with <br>
    html_change_log = change_log_str.replace("\n", "<br>")
    
    body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
    </head>
    <body>
        <h2>Timetable System Alert</h2>
        <p>Hello,</p>
        <p>This is an automated notification from the Timetable Management System.</p>
        <p><strong>Recent modifications have been applied to the timetable:</strong></p>
        
        <div style="background-color: #f9fafb; padding: 15px; border-radius: 8px; border: 1px solid #e5e7eb; margin: 15px 0;">
            <code style="font-family: monospace; font-size: 14px;">{html_change_log}</code>
        </div>
        
        <p>Please log in to the Timetable Application portal to view the newly published schedule.</p>
        <br>
        <p>Regards,<br><strong>Timetable Administrator</strong></p>
    </body>
    </html>
    """

    # Dispatch via Brevo
    await send_email(subject, body, TEST_RECIPIENTS)
