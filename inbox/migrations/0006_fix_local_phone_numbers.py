"""Perbaiki kontak yang tersimpan dengan format lokal (0812... / 812...) menjadi format WhatsApp (62812...).

Bila nomor tujuan sudah ada (pelanggan sempat chat dari 62812...), kontak dan percakapannya digabung.
"""
import re

from django.db import migrations
from django.db.models import Max


def _normalize(v):
    d = re.sub(r"\D", "", v or "")
    if d.startswith("00"):
        return d[2:]
    if d.startswith("0"):
        return "62" + d[1:]
    if d.startswith("8") and len(d) <= 13:
        return "62" + d
    return d


def forwards(apps, schema_editor):
    Contact = apps.get_model("inbox", "Contact")
    Conversation = apps.get_model("inbox", "Conversation")
    Message = apps.get_model("inbox", "Message")
    for c in Contact.objects.all():
        target = _normalize(c.wa_id)
        if target == c.wa_id:
            continue
        keep = Contact.objects.filter(wa_id=target).first()
        if not keep:
            Contact.objects.filter(pk=c.pk).update(wa_id=target)
            continue
        if not keep.name and c.name:
            Contact.objects.filter(pk=keep.pk).update(name=c.name)
        for conv in Conversation.objects.filter(contact=c):
            other = Conversation.objects.filter(contact=keep, outlet_id=conv.outlet_id).first()
            if not other:
                Conversation.objects.filter(pk=conv.pk).update(contact=keep)
                continue
            Message.objects.filter(conversation=conv).update(conversation=other)
            agg = Message.objects.filter(conversation=other).aggregate(last=Max("created_at"))
            Conversation.objects.filter(pk=other.pk).update(last_message_at=agg["last"])
            conv.delete()
        c.delete()


class Migration(migrations.Migration):
    dependencies = [("inbox", "0005_outlet_sync_blocked")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
