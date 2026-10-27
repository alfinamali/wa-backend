"""Pemrosesan payload webhook dan onboarding nomor outlet.

Idempoten: payload yang sama dua kali tidak menggandakan data (dicek lewat wamid).

Field webhook yang ditangani:
- messages            pesan masuk dari pelanggan + status kirim
- smb_message_echoes  pesan yang dikirim staf dari aplikasi WhatsApp Business di HP (coexistence)
- history             riwayat chat HP s.d. 180 hari, dikirim bertahap setelah sinkron diminta
- smb_app_state_sync  kontak dari aplikasi HP
- account_update      PARTNER_REMOVED / ACCOUNT_OFFBOARDED / ACCOUNT_RECONNECTED
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone as dt_tz

from django.conf import settings

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from . import whatsapp
from .models import Contact, Conversation, Message, MessageTemplate, Outlet, WhatsAppAccount
from .realtime import touch

log = logging.getLogger(__name__)
ORDER = {"queued": 0, "sent": 1, "delivered": 2, "read": 3, "failed": 4}
HISTORY_STATUS = {"DELIVERED": "delivered", "READ": "read", "PLAYED": "read", "SENT": "sent", "ERROR": "failed"}


def _at(ts):
    try:
        return datetime.fromtimestamp(int(ts), tz=dt_tz.utc)
    except (TypeError, ValueError):
        return timezone.now()


def _body(m):
    mtype = m.get("type", "text")
    data = m.get(mtype) or {}
    if mtype == "text":
        return mtype, data.get("body", ""), ""
    if mtype == "media_placeholder":
        return mtype, "[media]", ""
    return mtype, (data.get("caption") or f"[{mtype}]"), data.get("id", "")


def _media_meta(m):
    """mime_type & filename dari payload media WhatsApp (image/video/audio/document/sticker)."""
    data = m.get(m.get("type", "text")) or {}
    if not isinstance(data, dict) or not data.get("id"):
        return {}
    return {"mime_type": (data.get("mime_type") or "")[:120], "filename": (data.get("filename") or "")[:255]}


def _contact(wa_id, name=""):
    contact, _ = Contact.objects.get_or_create(wa_id=wa_id, defaults={"name": name})
    if name and not contact.name:
        Contact.objects.filter(pk=contact.pk).update(name=name)
    return contact


# ---------------- Webhook ----------------
def process_payload(payload):
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            field = change.get("field", "messages")
            v = change.get("value", {}) or {}
            if field == "account_update":
                handle_account_update(entry, v)
                continue
            pnid = v.get("metadata", {}).get("phone_number_id")
            outlet = Outlet.objects.filter(phone_number_id=pnid).first() if pnid else None
            if not outlet:
                log.warning("phone_number_id tidak dikenal: %s (field=%s)", pnid, field)
                continue
            if field == "smb_message_echoes":
                for m in v.get("message_echoes", []):
                    save_echo(outlet, m)
            elif field == "history":
                for h in v.get("history", []):
                    save_history(outlet, h)
            elif field == "smb_app_state_sync":
                for item in v.get("state_sync", []):
                    save_state_sync(item)
            else:  # messages
                for m in v.get("messages", []):
                    if m.get("type") in ("edit", "revoke", "unsupported") and not m.get("text"):
                        continue
                    save_inbound(outlet, v.get("contacts", []), m)
                for s in v.get("statuses", []):
                    save_status(s)


@transaction.atomic
def save_inbound(outlet, contacts, m):
    if Message.objects.filter(wamid=m["id"]).exists():
        return None
    wa_id = m["from"]
    name = next((c.get("profile", {}).get("name", "") for c in contacts if c.get("wa_id") == wa_id), "")
    contact = _contact(wa_id, name)
    at = _at(m.get("timestamp"))
    conv, _ = Conversation.objects.get_or_create(outlet=outlet, contact=contact)
    prev_in = conv.last_inbound_at
    mtype, body, media_id = _body(m)
    msg = Message.objects.create(
        conversation=conv, wamid=m["id"], direction="in", type=mtype, body=body, status="delivered",
        media_id=media_id, created_at=at, source=Message.Source.CUSTOMER, **_media_meta(m))
    touch(conv.pk, outlet.pk, "message",
          unread_count=F("unread_count") + 1, last_message_at=at, last_inbound_at=at, status=Conversation.Status.OPEN)
    cooldown = timedelta(hours=settings.AUTO_REPLY_COOLDOWN_HOURS)
    if outlet.auto_reply and outlet.is_active and outlet.auto_reply_text.strip() and (not prev_in or at - prev_in >= cooldown):
        transaction.on_commit(lambda: send_auto_reply(conv.pk))
    return msg


def send_auto_reply(conv_id):
    """Kirim balasan otomatis. Gagal kirim dicatat sebagai pesan 'failed', tidak menggagalkan webhook."""
    conv = Conversation.objects.select_related("outlet", "contact").get(pk=conv_id)
    text = conv.outlet.auto_reply_text.strip()
    msg = Message.objects.create(conversation=conv, direction="out", type="text", body=text,
                                 status=Message.Status.QUEUED, source=Message.Source.AUTO)
    try:
        wamid = whatsapp.send_text(conv.outlet.phone_number_id, conv.contact.wa_id, text, token=conv.outlet.token)
    except whatsapp.WhatsAppError as e:
        Message.objects.filter(pk=msg.pk).update(status=Message.Status.FAILED, error_detail=str(e)[:255])
        touch(conv.pk, conv.outlet_id, "status")
        log.warning("Balasan otomatis gagal (%s): %s", conv, e)
        return None
    Message.objects.filter(pk=msg.pk).update(wamid=wamid, status=Message.Status.SENT)
    touch(conv.pk, conv.outlet_id, "message", last_message_at=msg.created_at)
    return msg


@transaction.atomic
def save_echo(outlet, m):
    """Pesan yang staf kirim dari HP. Tampil di dashboard sebagai pesan keluar 'dari HP'."""
    if not m.get("id") or Message.objects.filter(wamid=m["id"]).exists():
        return None
    to = m.get("to")
    if not to:
        return None
    contact = _contact(to)
    at = _at(m.get("timestamp"))
    conv, _ = Conversation.objects.get_or_create(outlet=outlet, contact=contact)
    mtype, body, media_id = _body(m)
    msg = Message.objects.create(
        conversation=conv, wamid=m["id"], direction="out", type=mtype, body=body, status="sent",
        media_id=media_id, created_at=at, source=Message.Source.PHONE, **_media_meta(m))
    # dibalas dari HP = sudah ditangani; jangan tambah unread
    touch(conv.pk, outlet.pk, "message", last_message_at=at, unread_count=0)
    return msg


def save_history(outlet, h):
    if h.get("errors"):
        e = h["errors"][0]
        log.warning("History %s ditolak/gagal: %s %s", outlet, e.get("code"), e.get("title"))
        return 0
    meta = h.get("metadata", {}) or {}
    try:
        progress = int(meta.get("progress", 0))
        Outlet.objects.filter(pk=outlet.pk, history_progress__lt=progress).update(history_progress=progress)
    except (TypeError, ValueError):
        pass
    n = 0
    for thread in h.get("threads", []):
        customer = thread.get("id")
        if not customer:
            continue
        with transaction.atomic():
            conv, _ = Conversation.objects.get_or_create(outlet=outlet, contact=_contact(customer))
            last_at = last_in = None
            for m in thread.get("messages", []):
                if not m.get("id") or Message.objects.filter(wamid=m["id"]).exists():
                    continue
                out = m.get("from") != customer  # thread.id = nomor pelanggan
                at = _at(m.get("timestamp"))
                mtype, body, media_id = _body(m)
                st = HISTORY_STATUS.get((m.get("history_context") or {}).get("status", "").upper(),
                                        "sent" if out else "delivered")
                Message.objects.create(conversation=conv, wamid=m["id"], direction="out" if out else "in",
                                       type=mtype, body=body, media_id=media_id, status=st, created_at=at, **_media_meta(m),
                                       source=Message.Source.HISTORY)
                n += 1
                last_at = max(last_at, at) if last_at else at
                if not out:
                    last_in = max(last_in, at) if last_in else at
            if last_at:
                c = Conversation.objects.get(pk=conv.pk)
                upd = {}
                if not c.last_message_at or last_at > c.last_message_at:
                    upd["last_message_at"] = last_at
                if last_in and (not c.last_inbound_at or last_in > c.last_inbound_at):
                    upd["last_inbound_at"] = last_in
                touch(conv.pk, outlet.pk, "history", **upd)
    return n


def save_state_sync(item):
    if item.get("type") != "contact":
        return
    c = item.get("contact", {}) or {}
    wa_id = "".join(ch for ch in str(c.get("phone_number", "")) if ch.isdigit())
    if not wa_id or item.get("action") == "remove":
        return
    name = c.get("full_name") or c.get("first_name") or ""
    contact, created = Contact.objects.get_or_create(wa_id=wa_id, defaults={"name": name})
    if not created and name and contact.name != name:
        Contact.objects.filter(pk=contact.pk).update(name=name)  # nama dari buku kontak HP lebih akurat


def handle_account_update(entry, v):
    event = v.get("event", "")
    phone = "".join(ch for ch in str(v.get("phone_number", "")) if ch.isdigit())
    outlets = [o for o in Outlet.objects.all() if phone and o.number_digits == phone]
    if not outlets and entry.get("id"):
        outlets = list(Outlet.objects.filter(account__waba_id=entry["id"]))
    log.info("account_update %s untuk %s", event, [o.id for o in outlets])
    for o in outlets:
        if event in ("PARTNER_REMOVED", "ACCOUNT_OFFBOARDED"):
            info = v.get("disconnection_info", {}) or {}
            reason = info.get("reason") or event
            Outlet.objects.filter(pk=o.pk).update(is_active=False, disconnected_reason=str(reason)[:120])
        elif event == "ACCOUNT_RECONNECTED":
            Outlet.objects.filter(pk=o.pk).update(is_active=True, disconnected_reason="")


def save_status(s):
    new = s.get("status")
    if new not in ORDER:
        return
    msg = Message.objects.filter(wamid=s.get("id")).first()
    if not msg:
        return
    if new != "failed" and ORDER.get(msg.status, 0) >= ORDER[new]:
        return  # status datang terlambat/terbalik; jangan diturunkan
    err = ""
    if s.get("errors"):
        e = s["errors"][0]
        err = f'{e.get("code")}: {e.get("title")}'[:255]
    Message.objects.filter(pk=msg.pk).update(status=new, error_detail=err)
    touch(msg.conversation_id, kind="status")


# ---------------- Media ----------------
def open_media(msg):
    """Ambil isi file sebuah pesan. Mengembalikan (iterator bytes, mime_type, ukuran|None).

    Saat ini selalu dari Meta (URL sementara, diunduh dengan token outlet). Titik ini sengaja dipisah:
    penyimpanan permanen (mis. Cloudflare R2) nanti cukup dicek di sini lebih dulu.
    """
    token = msg.conversation.outlet.token
    info = whatsapp.media_info(msg.media_id, token)
    up = whatsapp.download_media(info["url"], token)
    mime = info.get("mime_type") or msg.mime_type or up.headers.get("Content-Type") or "application/octet-stream"
    size = info.get("file_size") or up.headers.get("Content-Length")

    def chunks():
        try:
            yield from up.iter_content(64 * 1024)
        finally:
            up.close()
    if not msg.mime_type or (size and not msg.file_size):
        Message.objects.filter(pk=msg.pk).update(mime_type=(msg.mime_type or mime)[:120],
                                                 file_size=msg.file_size or (int(size) if size else None))
    return chunks(), mime, (int(size) if size else None)


# ---------------- Template ----------------
def template_sources():
    """(waba_id, token) unik: WABA default dari .env + semua WABA hasil Embedded Signup."""
    seen, out = set(), []
    if settings.WA_WABA_ID:
        seen.add(settings.WA_WABA_ID)
        out.append((settings.WA_WABA_ID, None))
    for a in WhatsAppAccount.objects.exclude(access_token=""):
        if a.waba_id not in seen:
            seen.add(a.waba_id)
            out.append((a.waba_id, a.access_token))
    return out


def sync_templates():
    n = 0
    for waba_id, token in template_sources():
        for t in whatsapp.fetch_templates(waba_id, token):
            MessageTemplate.objects.update_or_create(
                waba_id=waba_id, name=t["name"], language=t["language"],
                defaults={"category": t["category"], "status": t["status"], "body": t["body"]})
            n += 1
    return n


# ---------------- Onboarding (Embedded Signup) ----------------
SYNC_LABEL = {"smb_app_state_sync": "kontak", "history": "riwayat chat"}
SYNC_BLOCKED_MSG = ("Meta menolak sinkron {what}: untuk nomor ini sinkron sudah pernah diminta (hanya boleh sekali) "
                    "atau sudah lewat 24 jam sejak terhubung.")


def _sync_refused(err):
    """Error yang tidak akan berubah bila diulang (bukan gangguan sementara)."""
    t = str(err).lower()
    return "(#4)" in t or "request limit" in t or "already" in t or "expired" in t


def request_coexist_sync(outlet):
    """Minta Meta mengirim kontak + riwayat chat HP. Sekali saja, maksimal 24 jam setelah onboarding."""
    errors, refused = [], []
    for field, sync_type in (("contacts_sync_requested_at", "smb_app_state_sync"),
                             ("history_sync_requested_at", "history")):
        if getattr(outlet, field):
            continue
        try:
            whatsapp.request_sync(outlet.phone_number_id, sync_type, outlet.token)
            Outlet.objects.filter(pk=outlet.pk).update(**{field: timezone.now()})
            setattr(outlet, field, timezone.now())
        except whatsapp.WhatsAppError as e:
            log.warning("Sinkron %s untuk %s ditolak: %s", sync_type, outlet, e)
            if _sync_refused(e):
                refused.append(SYNC_LABEL[sync_type])
            else:
                errors.append(f"Sinkron {SYNC_LABEL[sync_type]} gagal: {e}")
    blocked = SYNC_BLOCKED_MSG.format(what=" dan ".join(refused)) if refused else ""
    if blocked != outlet.sync_blocked:
        Outlet.objects.filter(pk=outlet.pk).update(sync_blocked=blocked)
        outlet.sync_blocked = blocked
    return errors + ([blocked] if blocked else [])


def pick_waba(token):
    """Bila event Meta tidak membawa waba_id: ambil dari izin token. Utamakan WABA yang belum terdaftar."""
    ids = whatsapp.wabas_from_token(token)
    if not ids:
        raise whatsapp.WhatsAppError("Token dari Meta tidak memberi akses ke akun WhatsApp mana pun. Ulangi dan centang akun WhatsApp-nya.")
    known = set(WhatsAppAccount.objects.filter(waba_id__in=ids).values_list("waba_id", flat=True))
    fresh = [i for i in ids if i not in known]
    return (fresh or ids)[-1]


def pick_phone_number(waba_id, token, outlet=None):
    """Pilih nomor di WABA bila Meta tidak mengirim phone_number_id.

    Prioritas: nomor aplikasi HP (is_on_biz_app) yang belum dipakai outlet lain, lalu nomor lain yang belum dipakai.
    """
    numbers = whatsapp.list_phone_numbers(waba_id, token)
    if not numbers:
        raise whatsapp.WhatsAppError("Akun WhatsApp ini belum punya nomor. Ulangi dan pilih/hubungkan nomornya di jendela Meta.")
    used = set(Outlet.objects.exclude(pk=getattr(outlet, "pk", None)).values_list("phone_number_id", flat=True))
    free = [n for n in numbers if n.get("id") not in used]
    if not free:
        raise whatsapp.WhatsAppError("Semua nomor di akun WhatsApp ini sudah terhubung ke outlet lain.")
    free.sort(key=lambda n: not n.get("is_on_biz_app"))
    return free[0]["id"]


def onboard(*, code, waba_id, phone_number_id, business_id="", event="", outlet=None, name="", city=""):
    """Selesaikan Embedded Signup: tukar token, subscribe webhook, simpan/ikat outlet, mulai sinkron.

    Mengembalikan (outlet, warnings). Melempar WhatsAppError bila langkah wajib gagal.
    """
    coexist = event == "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING"
    token = whatsapp.exchange_code(code)
    if not waba_id:
        waba_id = pick_waba(token)
    whatsapp.subscribe_app(waba_id, token)
    if not phone_number_id:
        phone_number_id = pick_phone_number(waba_id, token, outlet)
    info = whatsapp.phone_info(phone_number_id, token)
    if info.get("is_on_biz_app"):
        coexist = True
    warnings = []
    try:
        waba_name = whatsapp.waba_info(waba_id, token).get("name", "")
    except whatsapp.WhatsAppError:
        waba_name = ""

    account, _ = WhatsAppAccount.objects.update_or_create(
        waba_id=waba_id, defaults={"business_id": business_id or "", "access_token": token, "name": waba_name})

    if not coexist:
        # Nomor baru (bukan dari aplikasi HP) harus di-register ke Cloud API dengan PIN 6 digit.
        try:
            whatsapp.register_number(phone_number_id, f"{secrets.randbelow(10**6):06d}", token)
        except whatsapp.WhatsAppError as e:
            warnings.append(f"Register nomor: {e}")

    display = info.get("display_phone_number", "")
    with transaction.atomic():
        existing = Outlet.objects.filter(phone_number_id=phone_number_id).first()
        if outlet and existing and existing.pk != outlet.pk:
            raise whatsapp.WhatsAppError(f"Nomor ini sudah dipakai outlet '{existing.name}'.")
        outlet = outlet or existing or Outlet(name=name or info.get("verified_name") or display or "Outlet baru")
        if name:
            outlet.name = name
        if city:
            outlet.city = city
        outlet.phone_number_id = phone_number_id
        outlet.display_number = display or outlet.display_number or phone_number_id
        outlet.verified_name = info.get("verified_name", "")
        outlet.account = account
        outlet.mode = Outlet.Mode.COEXIST if coexist else Outlet.Mode.CLOUD
        outlet.is_active = True
        outlet.connected_at = timezone.now()
        outlet.disconnected_reason = ""
        if coexist:  # onboarding ulang = jendela sinkron baru
            outlet.contacts_sync_requested_at = outlet.history_sync_requested_at = None
            outlet.history_progress = 0
            outlet.sync_blocked = ""
        outlet.save()

    if coexist:
        warnings += request_coexist_sync(outlet)
    try:
        for t in whatsapp.fetch_templates(waba_id, token):
            MessageTemplate.objects.update_or_create(
                waba_id=waba_id, name=t["name"], language=t["language"],
                defaults={"category": t["category"], "status": t["status"], "body": t["body"]})
    except whatsapp.WhatsAppError as e:
        warnings.append(f"Sinkron template: {e}")
    return outlet, warnings
