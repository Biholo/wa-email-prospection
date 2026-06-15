import os
import re

from supabase import Client, create_client

AUDIT_BUCKET = "audit"
SCREENSHOTS_BUCKET = "audit-screenshots"


class SupabaseService:
    def __init__(self):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY are required")
        self._client: Client = create_client(url, key)

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def upload_pdf(self, filename: str, pdf_bytes: bytes) -> str:
        """Upload PDF to Supabase Storage 'audit' bucket. Returns public URL."""
        path = f"audits/{filename}"
        self._client.storage.from_(AUDIT_BUCKET).upload(
            path,
            pdf_bytes,
            {"content-type": "application/pdf", "x-upsert": "true"},
        )
        return self._client.storage.from_(AUDIT_BUCKET).get_public_url(path)

    def upload_screenshot(self, filename: str, png_bytes: bytes) -> str:
        """Upload PNG screenshot to 'audit-screenshots' bucket. Returns public URL."""
        self._client.storage.from_(SCREENSHOTS_BUCKET).upload(
            filename,
            png_bytes,
            {"content-type": "image/png", "x-upsert": "true"},
        )
        return self._client.storage.from_(SCREENSHOTS_BUCKET).get_public_url(filename)

    # ------------------------------------------------------------------
    # audits table
    # ------------------------------------------------------------------

    def log_audit(self, data: dict) -> str | None:
        """Insert audit result row. Returns new row id."""
        try:
            res = self._client.table("audits").insert(data).execute()
            return res.data[0]["id"] if res.data else None
        except Exception:
            return None

    def get_audit(self, audit_id: str) -> dict | None:
        try:
            res = self._client.table("audits").select("*").eq("id", audit_id).limit(1).execute()
            return res.data[0] if res.data else None
        except Exception:
            return None

    def update_audit_email(self, audit_id: str, email: str) -> None:
        try:
            self._client.table("audits").update({"email": email}).eq("id", audit_id).execute()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # emails table
    # ------------------------------------------------------------------

    def log_email(self, data: dict) -> str | None:
        """Insert email send record. Returns new row id."""
        try:
            res = self._client.table("emails").insert(data).execute()
            return res.data[0]["id"] if res.data else None
        except Exception:
            return None

    def update_email_event(self, resend_id: str, updates: dict) -> None:
        try:
            self._client.table("emails").update(updates).eq("resend_id", resend_id).execute()
        except Exception:
            pass

    def handle_email_opened(self, resend_id: str, opened_at: str) -> None:
        """Increment open_count; set opened_at + status only on first open."""
        try:
            res = self._client.table("emails").select("open_count, opened_at").eq("resend_id", resend_id).execute()
            if not res.data:
                return
            row = res.data[0]
            updates: dict = {"open_count": (row.get("open_count") or 0) + 1}
            if not row.get("opened_at"):
                updates["opened_at"] = opened_at
                updates["status"] = "opened"
            self._client.table("emails").update(updates).eq("resend_id", resend_id).execute()
        except Exception:
            pass

    def handle_email_clicked(self, resend_id: str, clicked_at: str) -> None:
        """Increment click_count; set clicked_at + status only on first click."""
        try:
            res = self._client.table("emails").select("click_count, clicked_at").eq("resend_id", resend_id).execute()
            if not res.data:
                return
            row = res.data[0]
            updates: dict = {"click_count": (row.get("click_count") or 0) + 1}
            if not row.get("clicked_at"):
                updates["clicked_at"] = clicked_at
                updates["status"] = "clicked"
            self._client.table("emails").update(updates).eq("resend_id", resend_id).execute()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # wa_messages table
    # ------------------------------------------------------------------

    def log_wa_message(self, data: dict) -> str | None:
        """Insert WA message send record. Returns new row id."""
        try:
            res = self._client.table("wa_messages").insert(data).execute()
            return res.data[0]["id"] if res.data else None
        except Exception:
            return None

    def update_wa_event(self, wasender_msg_id: str, updates: dict) -> None:
        try:
            self._client.table("wa_messages").update(updates).eq("wasender_msg_id", wasender_msg_id).execute()
        except Exception:
            pass

    def update_wa_by_id(self, row_id: str, updates: dict) -> None:
        try:
            self._client.table("wa_messages").update(updates).eq("id", row_id).execute()
        except Exception:
            pass

    def find_wa_by_msg_id(self, wasender_msg_id: str) -> dict | None:
        try:
            res = self._client.table("wa_messages").select("*").eq("wasender_msg_id", wasender_msg_id).limit(1).execute()
            return res.data[0] if res.data else None
        except Exception:
            return None

    def find_wa_by_phone(self, phone: str) -> dict | None:
        """Find latest WA message for a phone number (tries +33… and 33… formats)."""
        try:
            digits = re.sub(r"\D", "", phone)
            for fmt in (f"+{digits}", digits):
                res = (
                    self._client.table("wa_messages")
                    .select("*")
                    .eq("phone", fmt)
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
                if res.data:
                    return res.data[0]
            return None
        except Exception:
            return None
