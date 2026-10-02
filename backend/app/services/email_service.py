"""Email service for notifying teachers of routine changes."""
import asyncio
import aiosmtplib
from email.message import EmailMessage
from typing import List, Dict, Any

from loguru import logger
from sqlalchemy.orm import Session

from app.models.teacher import Teacher
from app.actions.engine import EngineResult


# ============================================================
# GMAIL CONFIGURATION
# ============================================================
import os

SENDER_EMAIL = os.environ.get("SMTP_EMAIL", "timetableadmin71@gmail.com")
APP_PASSWORD = os.environ.get("SMTP_APP_PASSWORD", "lbfg qdqw uhfo wybk")

# Hardcoded test recipients as requested by the user
TEST_RECIPIENTS = ["ankitsen53806626@gmail.com", "ankitcursor478@gmail.com"]


async def send_email(subject: str, body: str, to_emails: List[str]):
    """Send an email to a list of recipients asynchronously using Gmail SMTP."""
    if not to_emails:
        return

    email = EmailMessage()
    email["From"] = SENDER_EMAIL
    email["To"] = ", ".join(to_emails)
    email["Subject"] = subject
    email.set_content(body)

    try:
        await aiosmtplib.send(
            email,
            hostname="smtp.gmail.com",
            port=587,
            start_tls=True,
            username=SENDER_EMAIL,
            password=APP_PASSWORD,
        )
        logger.info(f"Notification email sent successfully to {to_emails}.")
    except Exception as e:
        logger.error(f"Failed to send email to {to_emails}: {e}")


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
    and send them an email notification.
    """
    if not result.success and not result.partial_applied:
        return  # No changes applied

    # Collect all affected teacher short names
    affected_short_names = set()
    
    for r in result.results:
        if not r.success:
            continue
            
        before = r.before
        after = r.after
        
        # Handle dict wrapping from SWAP/INTERCHANGE actions
        if isinstance(before, dict) and "a" in before and "b" in before:
            affected_short_names.update(_extract_teachers_from_entry(before["a"]))
            affected_short_names.update(_extract_teachers_from_entry(before["b"]))
        else:
            affected_short_names.update(_extract_teachers_from_entry(before))
            
        if isinstance(after, dict) and "a" in after and "b" in after:
            affected_short_names.update(_extract_teachers_from_entry(after["a"]))
            affected_short_names.update(_extract_teachers_from_entry(after["b"]))
        else:
            affected_short_names.update(_extract_teachers_from_entry(after))

    if not affected_short_names:
        return

    # In a real implementation, we would query the Teacher -> User model to get their emails.
    # We will log the actual users who would be notified.
    teachers = db.query(Teacher).filter(Teacher.short_name.in_(affected_short_names)).all()
    actual_teacher_names = [t.full_name for t in teachers]
    logger.info(f"Routine changes detected. The following teachers are affected: {actual_teacher_names}")
    
    # As per user request, we use the specified emails.
    recipients = TEST_RECIPIENTS
    
    subject = "Notification: Routine Change in Timetable System"
    change_log_str = result.change_log if result.change_log is not None else "Unknown details"
    
    body = f"""Hello,

This is an automated notification from the Timetable Management System.

Your scheduled routine has been modified.
Changes summary:
{change_log_str}

Please log in to the Timetable Application portal to view your updated schedule.

Regards,
Timetable Management System
"""

    await send_email(subject, body, recipients)
