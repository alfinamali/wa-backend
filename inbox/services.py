"""Pemrosesan payload webhook. Idempoten: payload yang sama dua kali tidak menggandakan data."""
import logging
from datetime import datetime, timezone as dt_tz

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Contact, Conversation, Message, MessageTemplate, Outlet
from . import whatsapp

log = logging.getLogger(__name__)
ORDER = {"queued": 0, "sent": 1, "delivered": 2, "read": 3, "failed": 4}


def process_payload(payload):
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            v = change.get("value", {})
            pnid = v.get("metadata", {}).get("phone_number_id")
            outlet = Outlet.objects.filter(phone_number_id=pnid).first() if pnid else None
            if not outlet:
                log.warning("phone_number_id tidak dikenal: %s", pnid)
                continue
            for m in v.get("messages", []):
                save_inbound(outlet, v.get("contacts", []), m)
            for s in v.get("statuses", []):
                save_status(s)


@transaction.atomic
def save_inbound(outlet, contacts, m):
    if Message.objects.filter(wamid=m["id"]).exists():
        return None
    wa_id, mtype = m["from"], m.get("type", "text")
    name = next((c.get("profile", {}).get("name", "") for c in contacts if c.get("wa_id") == wa_id), "")
    contact, _ = Contact.objects.get_or_create(wa_id=wa_id, defaults={"name": name})
    if name and not contact.name:
        Contact.objects.filter(pk=contact.pk).update(name=name)
    at = datetime.fromtimestamp(int(m["timestamp"]), tz=dt_tz.utc)
    conv, _ = Conversation.objects.get_or_create(outlet=outlet, contact=contact)
    data = m.get(mtype) or {}
    body = data.get("body", "") if mtype == "text" else (data.get("caption") or f"[{mtype}]")
    msg = Message.objects.create(
        conversation=conv, wamid=m["id"], direction="in", type=mtype, body=body, status="delivered",
        media_id=data.get("id", "") if mtype != "text" else "", created_at=at)
    Conversation.objects.filter(pk=conv.pk).update(
        unread_count=F("unread_count") + 1, last_message_at=at, last_inbound_at=at,
        status=Conversation.Status.OPEN, updated_at=timezone.now())
    return msg


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
    Conversation.objects.filter(pk=msg.conversation_id).update(updated_at=timezone.now())


def sync_templates():
    n = 0
    for t in whatsapp.fetch_templates():
        MessageTemplate.objects.update_or_create(
            name=t["name"], language=t["language"],
            defaults={"category": t["category"], "status": t["status"], "body": t["body"]})
        n += 1
    return n
