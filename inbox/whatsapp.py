"""Klien tipis untuk WhatsApp Cloud API."""
import hashlib
import hmac

import requests
from django.conf import settings

WA_TIMEOUT = (5, 20)

class WhatsAppError(Exception):
    pass


def _graph():
    return f"https://graph.facebook.com/{settings.WA_GRAPH_VERSION}"


def _headers():
    return {"Authorization": f"Bearer {settings.WA_TOKEN}"}


def valid_signature(raw: bytes, header: str) -> bool:
    """Cek X-Hub-Signature-256. Tanpa App Secret, semua permintaan ditolak."""
    secret = settings.WA_APP_SECRET
    if not secret or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[7:], expected)


def _json(r):
    try:
        return r.json()
    except ValueError:
        return {}


def _send(phone_number_id, body):
    try:
        r = requests.post(
                f"{_graph()}/{phone_number_id}/messages",
                headers=_headers(),
                timeout=WA_TIMEOUT,
                json={
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    **body,
                },
            )
    except requests.RequestException as e:
        raise WhatsAppError(f"Tidak bisa menghubungi WhatsApp: {e}")
    data = _json(r)
    if not r.ok:
        raise WhatsAppError(data.get("error", {}).get("message", f"Gagal mengirim (HTTP {r.status_code})"))
    return data["messages"][0]["id"]


def send_text(phone_number_id, to, text):
    return _send(phone_number_id, {"to": to, "type": "text", "text": {"body": text}})


def send_template(phone_number_id, to, name, language):
    return _send(phone_number_id, {"to": to, "type": "template", "template": {"name": name, "language": {"code": language}}})


def fetch_templates():
    """Ambil template dari WABA. Mengembalikan list dict siap simpan."""
    try:
        r = requests.get(f"{_graph()}/{settings.WA_WABA_ID}/message_templates", headers=_headers(), params={"limit": 200}, timeout=20)
    except requests.RequestException as e:
        raise WhatsAppError(str(e))
    data = _json(r)
    if not r.ok:
        raise WhatsAppError(data.get("error", {}).get("message", "Gagal mengambil template"))
    out = []
    for t in data.get("data", []):
        body = next((c.get("text", "") for c in t.get("components", []) if c.get("type") == "BODY"), "")
        out.append({"name": t["name"], "language": t.get("language", "id"), "category": t.get("category", ""),
                    "status": t.get("status", "").lower(), "body": body})
    return out


def create_template(name, language, category, body):
    """Buat template di Meta (POST /{WABA_ID}/message_templates). Status awal PENDING."""
    url = f"https://graph.facebook.com/{settings.WA_GRAPH_VERSION}/{settings.WA_WABA_ID}/message_templates"
    payload = {
        "name": name,
        "language": language,
        "category": category,
        "components": [{"type": "BODY", "text": body}],
    }
    try:
        r = requests.post(url, json=payload, timeout=20,
                          headers={"Authorization": f"Bearer {settings.WA_TOKEN}"})
    except requests.RequestException as e:
        raise WhatsAppError(f"Gagal menghubungi Meta: {e}") from e
    try:
        data = r.json()
    except ValueError:
        data = {}
    if not r.ok:
        err = data.get("error", {})
        raise WhatsAppError(err.get("error_user_msg") or err.get("message") or f"HTTP {r.status_code}")
    return data  # {"id": "...", "status": "PENDING", "category": "UTILITY"}
