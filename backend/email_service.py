import os
import imaplib
import email
import email.message
import email.utils
from email.header import decode_header
import logging
from backend.database import get_db_cursor
from backend.config import GMAIL_IMAP_HOST, GMAIL_IMAP_PORT, GMAIL_USER, GMAIL_APP_PASSWORD

logger = logging.getLogger("ticketing_system.email_service")

# --- Department Routing Configuration ---
# Map email plus-address tags (e.g. username+tag@gmail.com) to Department names
DEPARTMENT_ROUTING_MAP = {
    "it": "Information Technology",
    "tech": "Information Technology",
    "hr": "Human Resources",
    "billing": "Finance & Accounting",
    "finance": "Finance & Accounting",
    "accounting": "Finance & Accounting",
    "ops": "Operations & Facilities",
    "facilities": "Operations & Facilities",
    "admin": "General / Administrative",
    "general": "General / Administrative",
    "support": "General / Administrative",
}

def decode_mime_words(header_value: str | None) -> str:
    """Decodes MIME encoded header strings (e.g. =?utf-8?B?...?=)."""
    if not header_value:
        return ""
    decoded_fragments = decode_header(header_value)
    result = []
    for fragment, charset in decoded_fragments:
        if isinstance(fragment, bytes):
            try:
                result.append(fragment.decode(charset or "utf-8", errors="replace"))
            except Exception:
                result.append(fragment.decode("latin-1", errors="replace"))
        else:
            result.append(str(fragment))
    return "".join(result).strip()

def extract_email_body(msg: email.message.Message) -> str:
    """Extracts plain text body or fallback HTML from an email message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            
            # Skip attachments
            if "attachment" in content_disposition:
                continue
                
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        return payload.decode(charset, errors="replace")
                    except Exception:
                        return payload.decode("latin-1", errors="replace")
            elif content_type == "text/html" and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        body = payload.decode(charset, errors="replace")
                    except Exception:
                        body = payload.decode("latin-1", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                body = payload.decode(charset, errors="replace")
            except Exception:
                body = payload.decode("latin-1", errors="replace")

    return body.strip()

def generate_ticket_code_helper(cur) -> str:
    cur.execute("SELECT MAX(id) as max_id FROM ticketing_system.tickets;")
    res = cur.fetchone()
    next_id = (res["max_id"] or 0) + 1
    return f"KT-{1000 + next_id}"

def sync_gmail_tickets(limit: int = 15, mark_as_read: bool = False) -> dict:
    """
    Connects to Gmail via IMAP, fetches recent unread emails, and converts them to tickets.
    Deduplicates using email Message-ID in PostgreSQL.
    """
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        return {
            "success": False,
            "synced_count": 0,
            "message": "GMAIL_APP_PASSWORD is not set in .env. Please configure your 16-character Google App Password to enable email ingestion."
        }

    try:
        # 1. Connect to Gmail IMAP server over SSL
        mail = imaplib.IMAP4_SSL(GMAIL_IMAP_HOST, GMAIL_IMAP_PORT)
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        mail.select("INBOX")
        
        # 2. Search for UNSEEN (unread) messages
        status, search_data = mail.search(None, "UNSEEN")
        if status != "OK" or not search_data or not search_data[0]:
            mail.logout()
            return {
                "success": True,
                "synced_count": 0,
                "message": "No new unread emails found in Gmail inbox."
            }

        all_mail_ids = search_data[0].split()
        # Process the newest emails up to the specified limit
        mail_ids = all_mail_ids[-limit:] if len(all_mail_ids) > limit else all_mail_ids
        mail_ids.reverse()
        synced_tickets = []

        with get_db_cursor(commit=True) as cur:
            for m_id in mail_ids:
                res, data = mail.fetch(m_id, "(RFC822)")
                if res != "OK":
                    continue
                
                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)

                # Extract Message-ID
                msg_id = msg.get("Message-ID", "").strip()
                if msg_id:
                    # Check if ticket already exists
                    cur.execute("SELECT id FROM ticketing_system.tickets WHERE email_message_id = %s;", (msg_id,))
                    if cur.fetchone():
                        logger.info(f"Skipping already ingested email: {msg_id}")
                        continue

                # Extract sender info
                from_header = decode_mime_words(msg.get("From", ""))
                from_name, from_email = email.utils.parseaddr(from_header)
                if not from_email:
                    from_email = from_header or "unknown@sender.com"
                if not from_name:
                    from_name = from_email

                # Extract subject and body
                subject = decode_mime_words(msg.get("Subject", "No Subject"))
                body = extract_email_body(msg)
                if not body:
                    body = "(No message content provided in email body)"

                # Filter out automated spam, notifications, and job boards
                lower_sender = from_email.lower()
                lower_from_name = from_name.lower()
                lower_sub = subject.lower()
                spam_keywords = [
                    "no-reply", "noreply", "donotreply", "do-not-reply",
                    "mailer-daemon", "jobalert", "indeed", "linkedin", "jobstreet"
                ]
                if any(k in lower_sender or k in lower_from_name for k in spam_keywords):
                    logger.info(f"Skipping automated newsletter/spam from: {from_email}")
                    if mark_as_read:
                        mail.store(m_id, "+FLAGS", "\\Seen")
                    continue

                if lower_sub.startswith("out of office") or lower_sub.startswith("automatic reply"):
                    logger.info(f"Skipping auto-reply: {subject}")
                    if mark_as_read:
                        mail.store(m_id, "+FLAGS", "\\Seen")
                    continue

                # Department routing based on recipient plus-addressing (+it, +hr, +billing, etc.)
                to_hdr = decode_mime_words(msg.get("To", "")).lower()
                cc_hdr = decode_mime_words(msg.get("Cc", "")).lower()
                deliv_hdr = decode_mime_words(msg.get("Delivered-To", "")).lower()
                all_recipients = f"{to_hdr} {cc_hdr} {deliv_hdr}"

                dept_target = None
                for tag, d_name in DEPARTMENT_ROUTING_MAP.items():
                    if f"+{tag}@" in all_recipients:
                        dept_target = d_name
                        break

                dept_row = None
                if dept_target:
                    cur.execute("SELECT id, name FROM ticketing_system.departments WHERE lower(name) = lower(%s) LIMIT 1;", (dept_target,))
                    dept_row = cur.fetchone()

                # Dynamic fallback: check if any active department name matches a plus-address tag
                if not dept_row:
                    cur.execute("SELECT id, name FROM ticketing_system.departments WHERE is_active = TRUE;")
                    all_depts = cur.fetchall()
                    for d in all_depts:
                        slug = d["name"].lower().replace(" ", "").replace("&", "").replace("/", "")
                        if f"+{slug}@" in all_recipients:
                            dept_row = d
                            break

                # STRICT RULE: Only ingest if email was explicitly sent to a department alias (+it, +hr, +billing, etc.)
                if not dept_row:
                    logger.info(f"Skipping non-department email: '{subject}' (To: {all_recipients.strip() or 'unknown'})")
                    continue

                dept_id = dept_row["id"]
                dept_name = dept_row["name"]

                # Check if sender has an existing account in the system
                cur.execute("SELECT id FROM ticketing_system.users WHERE email = %s;", (from_email.lower(),))
                matched_user = cur.fetchone()
                req_id = matched_user["id"] if matched_user else None

                # Generate code and insert ticket
                ticket_code = generate_ticket_code_helper(cur)
                cur.execute(
                    """
                    INSERT INTO ticketing_system.tickets
                    (ticket_code, title, description, requester_email, requester_name, requester_id, department_id, department_name, source, status, priority, email_message_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'email', 'open', 'medium', %s)
                    RETURNING id, ticket_code, title, requester_email, requester_id, department_name, created_at;
                    """,
                    (ticket_code, subject, body, from_email.lower(), from_name, req_id, dept_id, dept_name, msg_id or None)
                )
                new_ticket = cur.fetchone()
                if new_ticket.get("created_at"):
                    new_ticket["created_at"] = new_ticket["created_at"].isoformat()
                synced_tickets.append(new_ticket)
                logger.info(f"Created ticket {ticket_code} ({dept_name}) from email: {subject}")

                # Optionally mark email as read in Gmail
                if mark_as_read:
                    mail.store(m_id, "+FLAGS", "\\Seen")

        mail.logout()
        return {
            "success": True,
            "synced_count": len(synced_tickets),
            "tickets": synced_tickets,
            "message": f"Successfully ingested {len(synced_tickets)} new ticket(s) from Gmail."
        }

    except imaplib.IMAP4.error as imap_err:
        logger.error(f"Gmail IMAP authentication/protocol error: {imap_err}")
        return {
            "success": False,
            "synced_count": 0,
            "message": f"Gmail IMAP error: {imap_err}. Please ensure 2FA and an App Password are used."
        }
    except Exception as e:
        logger.error(f"Failed to sync emails from Gmail: {e}")
        return {
            "success": False,
            "synced_count": 0,
            "message": f"Failed to sync emails: {str(e)}"
        }

if __name__ == "__main__":
    import sys
    # Ensure Windows console supports UTF-8 characters/emojis
    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    from backend.database import init_db_pool, close_db_pool
    print("=" * 60)
    print("Running Gmail Ticket Sync directly in VS Code...")
    print("=" * 60)
    init_db_pool()
    result = sync_gmail_tickets(limit=25, mark_as_read=True)
    print(f"Status: {result.get('message')}")
    if result.get("tickets"):
        for t in result["tickets"]:
            title = t.get('title', '').encode('ascii', 'replace').decode('ascii')
            print(f" -> [{t.get('ticket_code')}] {title} ({t.get('department_name', 'General')})")
    close_db_pool()

