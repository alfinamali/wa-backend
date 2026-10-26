"""Klien tipis untuk WhatsApp Cloud API.

Setiap fungsi menerima `token` opsional. Outlet yang dihubungkan lewat Embedded Signup punya token
sendiri (WhatsAppAccount.access_token); bila kosong dipakai WA_TOKEN dari .env.
"""
import hashlib
import hmac

import requests
from django.conf import settings

WA_TIMEOUT = (5, 20)


class WhatsAppError(Exception):
    pass


def _graph():
    return f"https://graph.facebook.com/{settings.WA_GRAPH_VERSION}"


def _headers(token=None):
    return {"Authorization": f"Bearer {token or settings.WA_TOKEN}"}


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


def _err(data, r):
    e = data.get("error", {}) if isinstance(data, dict) else {}
    return e.get("error_user_msg") or e.get("message") or f"HTTP {r.status_code}"


def _call(method, path, token=None, **kw):
    try:
        r = requests.request(method, f"{_graph()}/{path}", headers=_headers(token), timeout=WA_TIMEOUT, **kw)
    except requests.RequestException as e:
        raise WhatsAppError(f"Tidak bisa menghubungi Meta: {e}") from e
    data = _json(r)
    if not r.ok:
        raise WhatsAppError(_err(data, r))
    return data


# ---------------- Kirim pesan ----------------
def _send(phone_number_id, body, token=None):
    data = _call("POST", f"{phone_number_id}/messages", token,
                 json={"messaging_product": "whatsapp", "recipient_type": "individual", **body})
    return data["messages"][0]["id"]


def send_text(phone_number_id, to, text, token=None):
    return _send(phone_number_id, {"to": to, "type": "text", "text": {"body": text}}, token)


def send_template(phone_number_id, to, name, language, token=None):
    return _send(phone_number_id, {"to": to, "type": "template",
                                   "template": {"name": name, "language": {"code": language}}}, token)


# ---------------- Template (per WABA) ----------------
def fetch_templates(waba_id=None, token=None):
    """Ambil template dari WABA. Mengembalikan list dict siap simpan."""
    data = _call("GET", f"{waba_id or settings.WA_WABA_ID}/message_templates", token, params={"limit": 200})
    out = []
    for t in data.get("data", []):
        body = next((c.get("text", "") for c in t.get("components", []) if c.get("type") == "BODY"), "")
        out.append({"name": t["name"], "language": t.get("language", "id"), "category": t.get("category", ""),
                    "status": t.get("status", "").lower(), "body": body})
    return out


def create_template(name, language, category, body, waba_id=None, token=None):
    """Buat template di Meta (POST /{WABA_ID}/message_templates). Status awal PENDING."""
    return _call("POST", f"{waba_id or settings.WA_WABA_ID}/message_templates", token, json={
        "name": name, "language": language, "category": category,
        "components": [{"type": "BODY", "text": body}],
    })


# ---------------- Embedded Signup / coexistence ----------------
def exchange_code(code):
    """Tukar `code` dari FB.login (berlaku ~30 detik) menjadi business token pelanggan."""
    if not (settings.WA_APP_ID and settings.WA_APP_SECRET):
        raise WhatsAppError("WA_APP_ID dan WA_APP_SECRET wajib diisi di .env backend.")
    try:
        r = requests.get(f"{_graph()}/oauth/access_token", timeout=WA_TIMEOUT, params={
            "client_id": settings.WA_APP_ID, "client_secret": settings.WA_APP_SECRET, "code": code})
    except requests.RequestException as e:
        raise WhatsAppError(f"Tidak bisa menghubungi Meta: {e}") from e
    data = _json(r)
    if not r.ok or "access_token" not in data:
        raise WhatsAppError(f"Gagal menukar code: {_err(data, r)}")
    return data["access_token"]


def wabas_from_token(token):
    """ID WABA yang diizinkan untuk token ini (granular_scopes debug_token)."""
    try:
        r = requests.get(f"{_graph()}/debug_token", timeout=WA_TIMEOUT, params={
            "input_token": token, "access_token": f"{settings.WA_APP_ID}|{settings.WA_APP_SECRET}"})
    except requests.RequestException as e:
        raise WhatsAppError(f"Tidak bisa menghubungi Meta: {e}") from e
    data = _json(r)
    if not r.ok:
        raise WhatsAppError(f"debug_token gagal: {_err(data, r)}")
    ids = []
    for sc in data.get("data", {}).get("granular_scopes", []):
        if sc.get("scope") in ("whatsapp_business_management", "whatsapp_business_messaging"):
            ids += [i for i in sc.get("target_ids", []) if i not in ids]
    return ids


def subscribe_app(waba_id, token):
    """Daftarkan app kita ke WABA pelanggan agar webhook nomor itu masuk ke /webhook/."""
    return _call("POST", f"{waba_id}/subscribed_apps", token)


def phone_info(phone_number_id, token):
    return _call("GET", phone_number_id, token, params={
        "fields": "display_phone_number,verified_name,is_on_biz_app,platform_type"})


def list_phone_numbers(waba_id, token):
    data = _call("GET", f"{waba_id}/phone_numbers", token, params={
        "fields": "id,display_phone_number,verified_name,is_on_biz_app,platform_type"})
    return data.get("data", [])


def waba_info(waba_id, token):
    return _call("GET", waba_id, token, params={"fields": "name"})


def register_number(phone_number_id, pin, token):
    """Hanya untuk nomor baru (flow FINISH). Nomor coexistence TIDAK boleh di-register ulang."""
    return _call("POST", f"{phone_number_id}/register", token, json={"messaging_product": "whatsapp", "pin": pin})


def request_sync(phone_number_id, sync_type, token):
    """sync_type: 'smb_app_state_sync' (kontak) atau 'history' (riwayat chat).

    Hanya bisa sekali per jenis dan paling lambat 24 jam setelah onboarding.
    """
    return _call("POST", f"{phone_number_id}/smb_app_data", token,
                 json={"messaging_product": "whatsapp", "sync_type": sync_type})
