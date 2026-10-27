import hashlib
import hmac
import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Contact, Conversation, Message, MessageTemplate, Outlet, WhatsAppAccount
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
        send.assert_called_once_with("PN1", "6281234", "Halo juga", token="t")
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
        self.assertEqual(Message.objects.get().status, "failed")  # pesan gagal tetap dicatat

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


def change(field, value, pnid="PN1"):
    if pnid:
        value = {"metadata": {"phone_number_id": pnid, "display_phone_number": "6281100"}, **value}
    return {"entry": [{"id": "WABA1", "changes": [{"field": field, "value": value}]}]}


@override_settings(WA_APP_SECRET=SECRET)
class CoexistenceWebhookTests(TestCase):
    def setUp(self):
        self.outlet = Outlet.objects.create(name="Cabang", phone_number_id="PN1", display_number="+62 811-00",
                                            mode=Outlet.Mode.COEXIST)

    def post(self, data):
        raw = json.dumps(data).encode()
        sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        return self.client.post("/webhook/", raw, content_type="application/json", headers={"X-Hub-Signature-256": sig})

    def test_echo_from_phone_shows_as_outbound(self):
        ts = str(int(timezone.now().timestamp()))
        echo = change("smb_message_echoes", {"message_echoes": [
            {"from": "6281100", "to": "6281234", "id": "wamid.e1", "timestamp": ts, "type": "text", "text": {"body": "Dari HP"}}]})
        self.post(echo)
        self.post(echo)  # duplikat
        m = Message.objects.get()
        self.assertEqual((m.direction, m.source, m.body, m.conversation.contact.wa_id), ("out", "phone", "Dari HP", "6281234"))

    def test_history_direction_and_window(self):
        now = int(timezone.now().timestamp())
        self.post(change("history", {"history": [{"metadata": {"phase": 0, "chunk_order": 1, "progress": 40}, "threads": [
            {"id": "6281234", "messages": [
                {"from": "6281234", "id": "h1", "timestamp": str(now - 60), "type": "text", "text": {"body": "Halo"},
                 "history_context": {"status": "READ"}},
                {"from": "6281100", "id": "h2", "timestamp": str(now - 30), "type": "text", "text": {"body": "Ya"},
                 "history_context": {"status": "DELIVERED"}},
            ]}]}]}))
        msgs = list(Message.objects.order_by("created_at").values_list("direction", "source"))
        self.assertEqual(msgs, [("in", "history"), ("out", "history")])
        conv = Conversation.objects.get()
        self.assertTrue(conv.window_open)
        self.assertEqual(conv.unread_count, 0)
        self.outlet.refresh_from_db()
        self.assertEqual(self.outlet.history_progress, 40)

    def test_state_sync_updates_contact_name(self):
        Contact.objects.create(wa_id="6281234", name="")
        self.post(change("smb_app_state_sync", {"state_sync": [
            {"type": "contact", "action": "add", "contact": {"full_name": "Budi Santoso", "phone_number": "+62 812-34"},
             "metadata": {"timestamp": "1"}}]}))
        self.assertEqual(Contact.objects.get(wa_id="6281234").name, "Budi Santoso")

    def test_partner_removed_deactivates_outlet(self):
        self.post(change("account_update", {"phone_number": "6281100", "event": "PARTNER_REMOVED",
                                            "disconnection_info": {"reason": "PRIMARY_INACTIVITY", "initiated_by": "SYSTEM"}}, pnid=None))
        self.outlet.refresh_from_db()
        self.assertEqual((self.outlet.is_active, self.outlet.disconnected_reason), (False, "PRIMARY_INACTIVITY"))


@override_settings(WA_TOKEN="default", WA_WABA_ID="WABA_DEFAULT", WA_APP_ID="app", WA_APP_SECRET=SECRET)
class OnboardingTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.admin = U.objects.create_user("admin", password="x", is_staff=True)
        self.agent = U.objects.create_user("agent", password="x")
        self.api = APIClient()
        self.api.force_authenticate(self.admin)

    @mock.patch("inbox.whatsapp.fetch_templates", return_value=[
        {"name": "halo", "language": "id", "category": "UTILITY", "status": "approved", "body": "Halo"}])
    @mock.patch("inbox.whatsapp.request_sync", return_value={"request_id": "r"})
    @mock.patch("inbox.whatsapp.register_number")
    @mock.patch("inbox.whatsapp.waba_info", return_value={"name": "Toko A"})
    @mock.patch("inbox.whatsapp.phone_info", return_value={"display_phone_number": "+62 811-22", "verified_name": "Toko A",
                                                           "is_on_biz_app": True, "platform_type": "CLOUD_API"})
    @mock.patch("inbox.whatsapp.subscribe_app")
    @mock.patch("inbox.whatsapp.exchange_code", return_value="biz-token")
    def test_coexistence_signup_creates_outlet(self, exch, sub, info, waba, reg, sync, tpl):
        r = self.api.post("/api/outlets/embedded-signup/", {
            "code": "abc", "waba_id": "111", "phone_number_id": "222", "business_id": "333",
            "event": "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING", "name": "Outlet Bandung", "city": "Bandung"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        o = Outlet.objects.get(phone_number_id="222")
        self.assertEqual((o.name, o.mode, o.token, o.waba_id, o.display_number), ("Outlet Bandung", "coexist", "biz-token", "111", "+62 811-22"))
        sub.assert_called_once_with("111", "biz-token")
        reg.assert_not_called()  # nomor coexistence tidak boleh di-register ulang
        self.assertEqual([c.args[1] for c in sync.call_args_list], ["smb_app_state_sync", "history"])
        self.assertTrue(o.contacts_sync_requested_at and o.history_sync_requested_at)
        self.assertTrue(MessageTemplate.objects.filter(waba_id="111", name="halo").exists())
        self.assertNotIn("access_token", json.dumps(r.data))

    @mock.patch("inbox.whatsapp.send_text", return_value="wamid.x")
    def test_send_uses_outlet_token_and_templates_are_per_waba(self, send):
        acc = WhatsAppAccount.objects.create(waba_id="111", access_token="biz-token")
        o = Outlet.objects.create(name="A", phone_number_id="222", display_number="1", account=acc)
        conv = Conversation.objects.create(outlet=o, contact=Contact.objects.create(wa_id="6281"), last_inbound_at=timezone.now())
        self.api.post(f"/api/conversations/{conv.id}/messages/", {"text": "hai"}, format="json")
        send.assert_called_once_with("222", "6281", "hai", token="biz-token")
        MessageTemplate.objects.create(waba_id="111", name="a", body="a", status="approved")
        MessageTemplate.objects.create(waba_id="", name="lama", body="b", status="approved")
        names = [t["name"] for t in self.api.get(f"/api/templates/?outlet={o.id}").data]
        self.assertEqual(names, ["a"])

    def test_only_staff_can_manage_outlets(self):
        body = {"name": "Baru", "phone_number_id": "999", "display_number": "+62 1"}
        self.assertEqual(self.api.post("/api/outlets/", body, format="json").status_code, 201)
        oid = Outlet.objects.get(phone_number_id="999").id
        self.assertEqual(self.api.patch(f"/api/outlets/{oid}/", {"is_active": False}, format="json").status_code, 200)
        agent = APIClient()
        agent.force_authenticate(self.agent)
        self.assertEqual(agent.post("/api/outlets/", body, format="json").status_code, 403)
        self.assertEqual(agent.post("/api/outlets/embedded-signup/", {}, format="json").status_code, 403)


@override_settings(WA_TOKEN="t", WA_APP_SECRET=SECRET, AUTO_REPLY_COOLDOWN_HOURS=12)
class AccountAndAutoReplyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("budi", password="lama12345")
        self.api = APIClient()

    def login(self, password="lama12345"):
        r = self.api.post("/api/auth/login/", {"username": "budi", "password": password}, format="json")
        self.api.credentials(HTTP_AUTHORIZATION=f"Token {r.data['token']}")
        return r.data["token"]

    def test_logout_revokes_token(self):
        self.login()
        self.assertEqual(self.api.post("/api/auth/logout/").status_code, 200)
        self.assertEqual(self.api.get("/api/me/").status_code, 401)

    def test_update_profile_and_preferences(self):
        self.login()
        r = self.api.patch("/api/me/", {"name": "Budi Santoso", "email": "b@x.id", "preferences": {"sound": False, "x": 1}}, format="json")
        self.assertEqual((r.data["name"], r.data["email"], r.data["preferences"]), ("Budi Santoso", "b@x.id", {"notif": True, "sound": False}))
        self.assertEqual(r.data["role"], "Agen")

    def test_change_password_rotates_token(self):
        old = self.login()
        self.assertEqual(self.api.post("/api/me/password/", {"old_password": "salah", "new_password": "BaruSekali#2026"}, format="json").status_code, 400)
        r = self.api.post("/api/me/password/", {"old_password": "lama12345", "new_password": "BaruSekali#2026"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r.data["token"], old)
        self.assertTrue(get_user_model().objects.get(username="budi").check_password("BaruSekali#2026"))

    @mock.patch("inbox.whatsapp.send_text", return_value="wamid.auto")
    def test_auto_reply_once_per_cooldown(self, send):
        Outlet.objects.create(name="A", phone_number_id="PN1", display_number="1", auto_reply=True, auto_reply_text="Halo, tunggu ya")

        def post(wamid):
            raw = json.dumps(payload(wamid=wamid)).encode()
            sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post("/webhook/", raw, content_type="application/json", headers={"X-Hub-Signature-256": sig})

        post("w1")
        post("w2")  # masih dalam jeda 12 jam: tidak dibalas lagi
        send.assert_called_once_with("PN1", "6281234", "Halo, tunggu ya", token="t")
        self.assertEqual(Message.objects.filter(source="auto", status="sent").count(), 1)


@override_settings(WA_APP_ID="app", WA_APP_SECRET=SECRET)
class SignupWithoutPhoneIdTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(get_user_model().objects.create_user("adm", password="x", is_staff=True))

    @mock.patch("inbox.whatsapp.fetch_templates", return_value=[])
    @mock.patch("inbox.whatsapp.request_sync", return_value={})
    @mock.patch("inbox.whatsapp.waba_info", return_value={})
    @mock.patch("inbox.whatsapp.phone_info", return_value={"display_phone_number": "+62 812", "is_on_biz_app": True})
    @mock.patch("inbox.whatsapp.list_phone_numbers", return_value=[{"id": "900", "is_on_biz_app": False}, {"id": "901", "is_on_biz_app": True}])
    @mock.patch("inbox.whatsapp.subscribe_app")
    @mock.patch("inbox.whatsapp.exchange_code", return_value="tok")
    def test_phone_number_resolved_from_waba(self, *_):
        r = self.api.post("/api/outlets/embedded-signup/", {"code": "c", "waba_id": "134059993124073",
                          "event": "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING", "name": "apin", "city": "surabaya"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        o = Outlet.objects.get()
        self.assertEqual((o.phone_number_id, o.name, o.mode), ("901", "apin", "coexist"))

    def test_missing_code_has_clear_error(self):
        r = self.api.post("/api/outlets/embedded-signup/", {"waba_id": "1"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("code", r.data)


@override_settings(WA_APP_ID="app", WA_APP_SECRET=SECRET)
class SignupWithoutWabaIdTests(TestCase):
    @mock.patch("inbox.whatsapp.fetch_templates", return_value=[])
    @mock.patch("inbox.whatsapp.request_sync", return_value={})
    @mock.patch("inbox.whatsapp.waba_info", return_value={})
    @mock.patch("inbox.whatsapp.phone_info", return_value={"display_phone_number": "+62 812", "is_on_biz_app": True})
    @mock.patch("inbox.whatsapp.list_phone_numbers", return_value=[{"id": "901", "is_on_biz_app": True}])
    @mock.patch("inbox.whatsapp.subscribe_app")
    @mock.patch("inbox.whatsapp.wabas_from_token", return_value=["134059993124073"])
    @mock.patch("inbox.whatsapp.exchange_code", return_value="tok")
    def test_waba_resolved_from_token(self, exch, wabas, sub, *_):
        api = APIClient()
        api.force_authenticate(get_user_model().objects.create_user("adm", password="x", is_staff=True))
        r = api.post("/api/outlets/embedded-signup/", {"code": "c", "event": "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING", "name": "apin"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        sub.assert_called_once_with("134059993124073", "tok")
        o = Outlet.objects.get()
        self.assertEqual((o.waba_id, o.phone_number_id), ("134059993124073", "901"))


class _FakeRedis:
    """Redis tiruan minimal untuk publish/subscribe dalam satu proses."""
    def __init__(self):
        self.queue = []

    def publish(self, channel, data):
        self.queue.append({"type": "message", "channel": channel, "data": data})

    def pubsub(self, **kw):
        parent = self

        class P:
            def subscribe(self, *a):
                pass

            def get_message(self, timeout=0):
                return parent.queue.pop(0) if parent.queue else None

            def close(self):
                pass
        return P()


@override_settings(REALTIME_STREAM_SECONDS=4)
class RealtimeTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.agent = U.objects.create_user("agen", password="x")
        self.other = U.objects.create_user("lain", password="x")
        from rest_framework.authtoken.models import Token
        self.tok = Token.objects.create(user=self.agent).key
        self.tok_other = Token.objects.create(user=self.other).key
        self.outlet = Outlet.objects.create(name="A", phone_number_id="PN1", display_number="1")
        self.outlet.members.add(self.agent)
        self.conv = Conversation.objects.create(outlet=self.outlet, contact=Contact.objects.create(wa_id="6281"))

    def open(self, tok):
        r = self.client.get("/api/events/", headers={"Authorization": f"Token {tok}"})
        return r, iter(r.streaming_content)

    def next_event(self, it, name):
        for chunk in it:
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            if text.startswith(f"event: {name}"):
                return json.loads(text.split("data: ", 1)[1])
        return None

    def test_requires_token(self):
        self.assertEqual(self.client.get("/api/events/").status_code, 401)
        self.assertEqual(self.client.get("/api/events/", headers={"Authorization": "Token salah"}).status_code, 401)

    def test_db_mode_pushes_change(self):
        r, it = self.open(self.tok)
        self.assertEqual(r["Content-Type"], "text/event-stream")
        self.assertEqual(self.next_event(it, "hello"), {"mode": "db"})
        from .realtime import touch
        touch(self.conv.pk, self.outlet.pk, "message", unread_count=1)
        ev = self.next_event(it, "change")
        self.assertEqual(ev["conversations"], [self.conv.pk])

    def test_agent_does_not_see_other_outlets(self):
        r, it = self.open(self.tok_other)
        self.next_event(it, "hello")
        from .realtime import touch
        touch(self.conv.pk, self.outlet.pk, "message")
        self.assertIsNone(self.next_event(it, "change"))  # stream berakhir tanpa event

    def test_redis_mode_is_instant_and_filtered(self):
        fake = _FakeRedis()
        with mock.patch("inbox.realtime.redis_client", return_value=fake):
            r, it = self.open(self.tok)
            self.assertEqual(self.next_event(it, "hello"), {"mode": "redis"})
            other = Outlet.objects.create(name="B", phone_number_id="PN2", display_number="2")
            from .realtime import publish
            publish(999, other.pk)              # outlet lain: tidak boleh diteruskan
            publish(self.conv.pk, self.outlet.pk, "message")
            ev = self.next_event(it, "change")
        self.assertEqual(ev, {"conversation": self.conv.pk, "outlet": self.outlet.pk, "kind": "message"})

    @override_settings(WA_APP_SECRET=SECRET)
    def test_webhook_publishes_after_commit(self):
        with mock.patch("inbox.realtime.publish") as pub:
            raw = json.dumps(payload()).encode()
            sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post("/webhook/", raw, content_type="application/json", headers={"X-Hub-Signature-256": sig})
        conv = Conversation.objects.get(contact__wa_id="6281234")
        pub.assert_called_with(conv.pk, self.outlet.pk, "message")


@override_settings(WA_TOKEN="t", WA_APP_SECRET=SECRET)
class MediaTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.agent, self.other = U.objects.create_user("agen", password="x"), U.objects.create_user("lain", password="x")
        self.outlet = Outlet.objects.create(name="A", phone_number_id="PN1", display_number="1")
        self.outlet.members.add(self.agent)
        self.conv = Conversation.objects.create(outlet=self.outlet, contact=Contact.objects.create(wa_id="6281"),
                                                last_inbound_at=timezone.now())
        self.api = APIClient()
        self.api.force_authenticate(self.agent)
        self.url = f"/api/conversations/{self.conv.id}/media/"

    def file(self, name, mime, size=1000):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(name, b"x" * size, content_type=mime)

    @mock.patch("inbox.whatsapp.send_media", return_value="wamid.m1")
    @mock.patch("inbox.whatsapp.upload_media", return_value="MEDIA1")
    def test_send_image_with_caption(self, up, send):
        r = self.api.post(self.url, {"file": self.file("foto.jpg", "image/jpeg"), "caption": "Ini warnanya"}, format="multipart")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["type"], r.data["body"], r.data["has_media"], r.data["mime_type"]), ("image", "Ini warnanya", True, "image/jpeg"))
        send.assert_called_once_with("PN1", "6281", "image", "MEDIA1", caption="Ini warnanya", filename="foto.jpg", token="t")

    @mock.patch("inbox.whatsapp.send_media", return_value="wamid.m2")
    @mock.patch("inbox.whatsapp.upload_media", return_value="MEDIA2")
    def test_docx_with_generic_mime_is_detected(self, up, send):
        r = self.api.post(self.url, {"file": self.file("katalog.docx", "application/octet-stream")}, format="multipart")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["type"], r.data["filename"]), ("document", "katalog.docx"))

    def test_rejects_unsupported_and_too_big(self):
        r = self.api.post(self.url, {"file": self.file("x.exe", "application/x-msdownload")}, format="multipart")
        self.assertEqual(r.status_code, 400)
        r = self.api.post(self.url, {"file": self.file("besar.png", "image/png", 6 * 1024 * 1024)}, format="multipart")
        self.assertEqual(r.status_code, 400)
        self.assertIn("5 MB", r.data["detail"])

    @mock.patch("inbox.whatsapp.upload_media")
    def test_blocked_outside_window(self, up):
        Conversation.objects.filter(pk=self.conv.pk).update(last_inbound_at=timezone.now() - timedelta(hours=25))
        r = self.api.post(self.url, {"file": self.file("a.pdf", "application/pdf")}, format="multipart")
        self.assertEqual(r.status_code, 409)
        up.assert_not_called()

    @mock.patch("inbox.whatsapp.upload_media", side_effect=WhatsAppError("token salah"))
    def test_upload_error_marks_failed(self, up):
        r = self.api.post(self.url, {"file": self.file("a.pdf", "application/pdf")}, format="multipart")
        self.assertEqual(r.status_code, 502)
        self.assertEqual(Message.objects.get().status, "failed")

    def test_media_proxy_streams_file_and_checks_access(self):
        m = Message.objects.create(conversation=self.conv, direction="in", type="document", body="[document]",
                                   media_id="MID", filename="nota.pdf", mime_type="application/pdf")
        up = mock.Mock(headers={"Content-Length": "7"})
        up.iter_content.return_value = [b"PDF", b"DATA"]
        with mock.patch("inbox.whatsapp.media_info", return_value={"url": "https://lookaside/x", "mime_type": "application/pdf", "file_size": 7}) as info, \
             mock.patch("inbox.whatsapp.download_media", return_value=up) as dl:
            r = self.api.get(f"/api/media/{m.id}/?download=1")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(b"".join(r.streaming_content), b"PDFDATA")
            self.assertEqual(r["Content-Type"], "application/pdf")
            self.assertIn('attachment; filename="nota.pdf"', r["Content-Disposition"])
            info.assert_called_once_with("MID", "t")
            dl.assert_called_once_with("https://lookaside/x", "t")
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.get(f"/api/media/{m.id}/").status_code, 404)

    def test_expired_media_returns_410(self):
        m = Message.objects.create(conversation=self.conv, direction="in", type="image", body="[image]", media_id="OLD")
        with mock.patch("inbox.whatsapp.media_info", side_effect=WhatsAppError("not found")):
            self.assertEqual(self.api.get(f"/api/media/{m.id}/").status_code, 410)

    def test_inbound_document_keeps_filename_and_mime(self):
        raw = json.dumps({"entry": [{"changes": [{"field": "messages", "value": {
            "metadata": {"phone_number_id": "PN1"}, "contacts": [{"wa_id": "6281", "profile": {"name": "B"}}],
            "messages": [{"from": "6281", "id": "wamid.doc", "timestamp": str(int(timezone.now().timestamp())), "type": "document",
                          "document": {"id": "DOC1", "mime_type": "application/pdf", "filename": "invoice.pdf", "caption": "Ini invoice"}}]}}]}]}).encode()
        sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        self.client.post("/webhook/", raw, content_type="application/json", headers={"X-Hub-Signature-256": sig})
        m = Message.objects.get(wamid="wamid.doc")
        self.assertEqual((m.type, m.media_id, m.filename, m.mime_type, m.body), ("document", "DOC1", "invoice.pdf", "application/pdf", "Ini invoice"))
