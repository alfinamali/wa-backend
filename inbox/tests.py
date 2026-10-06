import hashlib
import hmac
import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Contact, Conversation, Message, MessageTemplate, Outlet
from .whatsapp import WhatsAppError

SECRET = "s3cret"


def payload(wamid="wamid.1", pnid="PN1", text="Halo"):
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": pnid},
        "contacts": [{"wa_id": "6281234", "profile": {"name": "Budi"}}],
        "messages": [{"from": "6281234", "id": wamid, "timestamp": str(int(timezone.now().timestamp())),
                      "type": "text", "text": {"body": text}}]}}]}]}


@override_settings(WA_APP_SECRET=SECRET, WA_VERIFY_TOKEN="vt")
class WebhookTests(TestCase):
    def setUp(self):
        self.outlet = Outlet.objects.create(name="Pusat", phone_number_id="PN1", display_number="+62 1")

    def post(self, data, sig=None):
        raw = json.dumps(data).encode()
        sig = sig or "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        return self.client.post("/webhook/", raw, content_type="application/json", headers={"X-Hub-Signature-256": sig})

    def test_verify(self):
        ok = self.client.get("/webhook/?hub.mode=subscribe&hub.verify_token=vt&hub.challenge=123")
        self.assertEqual((ok.status_code, ok.content), (200, b"123"))
        self.assertEqual(self.client.get("/webhook/?hub.mode=subscribe&hub.verify_token=salah&hub.challenge=1").status_code, 403)

    def test_bad_signature_rejected(self):
        self.assertEqual(self.post(payload(), sig="sha256=salah").status_code, 401)
        self.assertEqual(Message.objects.count(), 0)

    def test_inbound_creates_conversation_and_is_idempotent(self):
        self.assertEqual(self.post(payload()).status_code, 200)
        self.assertEqual(self.post(payload()).status_code, 200)  # kiriman ulang dari Meta
        conv = Conversation.objects.get()
        self.assertEqual((Message.objects.count(), conv.unread_count, conv.window_open), (1, 1, True))
        self.assertEqual(conv.contact.name, "Budi")

    def test_status_never_downgrades(self):
        self.post(payload())
        conv = Conversation.objects.get()
        Message.objects.create(conversation=conv, wamid="out1", direction="out", status="sent")
        st = lambda s: {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "PN1"}, "statuses": [{"id": "out1", "status": s}]}}]}]}
        self.post(st("delivered"))
        self.post(st("sent"))  # datang terlambat
        self.assertEqual(Message.objects.get(wamid="out1").status, "delivered")


@override_settings(WA_TOKEN="t", WA_APP_SECRET=SECRET)
class ApiTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.agent, self.other = U.objects.create_user("agent", password="x"), U.objects.create_user("other", password="x")
        self.outlet = Outlet.objects.create(name="Pusat", phone_number_id="PN1", display_number="+62 1")
        self.outlet.members.add(self.agent)
        self.conv = Conversation.objects.create(outlet=self.outlet, contact=Contact.objects.create(wa_id="6281234", name="Budi"),
                                                last_inbound_at=timezone.now(), last_message_at=timezone.now(), unread_count=2)
        self.api = APIClient()
        self.api.force_authenticate(self.agent)
        self.url = f"/api/conversations/{self.conv.id}/messages/"

    @mock.patch("inbox.whatsapp.send_text", return_value="wamid.out1")
    def test_send_text_in_window(self, send):
        r = self.api.post(self.url, {"text": "Halo juga"}, format="json")
        self.assertEqual(r.status_code, 201)
        send.assert_called_once_with("PN1", "6281234", "Halo juga")
        self.assertEqual(Message.objects.get(wamid="wamid.out1").sent_by, self.agent)

    @mock.patch("inbox.whatsapp.send_text")
    def test_text_blocked_outside_window(self, send):
        Conversation.objects.filter(pk=self.conv.pk).update(last_inbound_at=timezone.now() - timedelta(hours=25))
        self.assertEqual(self.api.post(self.url, {"text": "x"}, format="json").status_code, 409)
        send.assert_not_called()

    @mock.patch("inbox.whatsapp.send_template", return_value="wamid.t1")
    def test_template_only_when_approved(self, send):
        Conversation.objects.filter(pk=self.conv.pk).update(last_inbound_at=None)
        ok = MessageTemplate.objects.create(name="konfirmasi", language="id", status="approved", body="Halo")
        bad = MessageTemplate.objects.create(name="promo", language="id", status="pending", body="Promo")
        self.assertEqual(self.api.post(self.url, {"template": bad.id}, format="json").status_code, 400)
        r = self.api.post(self.url, {"template": ok.id}, format="json")
        self.assertEqual((r.status_code, r.data["is_template"]), (201, True))

    @mock.patch("inbox.whatsapp.send_text", side_effect=WhatsAppError("token salah"))
    def test_meta_error_becomes_502(self, _):
        self.assertEqual(self.api.post(self.url, {"text": "x"}, format="json").status_code, 502)
        self.assertEqual(Message.objects.count(), 0)

    def test_opening_conversation_marks_read(self):
        self.assertEqual(self.api.get(self.url).status_code, 200)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.unread_count, 0)

    def test_agent_cannot_see_other_outlets(self):
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.get("/api/conversations/").data, [])
        self.assertEqual(self.api.get(self.url).status_code, 404)

    def test_requires_login(self):
        self.assertEqual(APIClient().get("/api/conversations/").status_code, 401)

    def test_start_conversation_normalizes_number(self):
        r = self.api.post("/api/conversations/", {"outlet": self.outlet.id, "wa_id": "+62 812-9999-0000", "name": "Baru"}, format="json")
        self.assertEqual((r.status_code, r.data["wa_id"]), (201, "6281299990000"))
