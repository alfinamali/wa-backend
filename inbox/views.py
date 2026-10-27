import json
import logging
import mimetypes
import os

from django.conf import settings
from django.db.models import F, OuterRef, Q, Subquery, Sum
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.authtoken.models import Token
from rest_framework.response import Response

from . import whatsapp
from .models import WINDOW, Contact, Conversation, Message, MessageTemplate, Outlet, Profile
from .serializers import (ContactSerializer, ConversationSerializer, EmbeddedSignupSerializer, MessageSerializer,
                          OutletSerializer, PasswordChangeSerializer, ProfileUpdateSerializer,
                          StartConversationSerializer, TemplateCreateSerializer, TemplateSerializer)
from .realtime import stream as realtime_stream, touch
from .services import open_media, onboard, process_payload, request_coexist_sync, sync_templates

log = logging.getLogger(__name__)
LIMIT = 200


def visible_outlets(user):
    """Staf melihat semua outlet; agen hanya outlet yang mereka ikuti."""
    return Outlet.objects.all() if user.is_staff else Outlet.objects.filter(members=user)


# ---------------- Webhook Meta ----------------
@csrf_exempt
def webhook(request):
    if request.method == "GET":
        q = request.GET
        if settings.WA_VERIFY_TOKEN and q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == settings.WA_VERIFY_TOKEN:
            return HttpResponse(q.get("hub.challenge", ""), content_type="text/plain")
        return HttpResponse(status=403)

    if request.method == "POST":
        if not whatsapp.valid_signature(
            request.body,
            request.headers.get("X-Hub-Signature-256", "")
        ):
            log.warning("Webhook signature invalid")
            return HttpResponse(status=401)

        try:
            payload = json.loads(request.body)
        except ValueError:
            log.warning("Webhook JSON invalid")
            return HttpResponse(status=400)

        try:
            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    phone_number_id = value.get(
                        "metadata", {}
                    ).get("phone_number_id")

                    log.info(
                        "WhatsApp webhook received: phone_number_id=%s field=%s",
                        phone_number_id,
                        change.get("field"),
                    )

            process_payload(payload)

        except Exception:
            log.exception("Webhook gagal diproses")

        return HttpResponse(status=200)


# ---------------- Media (pratinjau/unduh) ----------------
@api_view(["GET"])
def media_file(request, pk):
    """Teruskan isi file dari Meta ke browser. Token WhatsApp tidak pernah sampai ke browser.

    `?download=1` -> Content-Disposition attachment (unduh), selain itu inline (pratinjau).
    """
    msg = get_object_or_404(Message.objects.select_related("conversation__outlet__account"), pk=pk,
                            conversation__outlet__in=visible_outlets(request.user))
    if not msg.media_id:
        return Response({"detail": "Pesan ini tidak punya file."}, status=404)
    try:
        chunks, mime, size = open_media(msg)
    except whatsapp.WhatsAppError as e:
        log.warning("Media %s gagal diambil: %s", msg.pk, e)
        return Response({"detail": "File tidak tersedia lagi di WhatsApp (sudah kedaluwarsa atau dihapus)."}, status=410)
    ext = mimetypes.guess_extension(mime.split(";")[0]) or ""
    filename = msg.filename or f"{msg.type}-{msg.pk}{ext}"
    resp = StreamingHttpResponse(chunks, content_type=mime)
    if size:
        resp["Content-Length"] = str(size)
    resp["Content-Disposition"] = content_disposition_header(bool(request.GET.get("download")), filename)
    resp["Cache-Control"] = "private, max-age=3600"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


# ---------------- Realtime (SSE) ----------------
def events(request):
    """Aliran Server-Sent Events. Auth lewat header `Authorization: Token ...` (bukan query string,
    agar token tidak tercatat di access log). Frontend memakai fetch-stream, bukan EventSource."""
    if request.method != "GET":
        return HttpResponse(status=405)
    auth = request.headers.get("Authorization", "")
    key = auth[6:].strip() if auth.startswith("Token ") else ""
    tok = Token.objects.select_related("user").filter(key=key).first() if key else None
    if not tok or not tok.user.is_active:
        return JsonResponse({"detail": "Token tidak valid."}, status=401)
    user = tok.user
    ids = [] if user.is_staff else list(visible_outlets(user).values_list("id", flat=True))
    resp = StreamingHttpResponse(realtime_stream(user.is_staff, ids), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache, no-transform"
    resp["X-Accel-Buffering"] = "no"  # matikan buffering proxy (nginx/Railway)
    return resp


# ---------------- API ----------------
def me_data(u):
    profile, _ = Profile.objects.get_or_create(user=u)
    return {"id": u.id, "username": u.username, "name": u.get_full_name() or u.username,
            "first_name": u.first_name, "last_name": u.last_name, "email": u.email,
            "is_staff": u.is_staff, "role": "Admin" if u.is_staff else "Agen",
            "preferences": profile.prefs(),
            "outlets": list(visible_outlets(u).values_list("id", flat=True))}


@api_view(["GET", "PATCH"])
def me(request):
    """GET profil. PATCH {name?, email?, preferences?: {notif, sound}}."""
    u = request.user
    if request.method == "PATCH":
        s = ProfileUpdateSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        if "name" in d:
            first, _, last = d["name"].strip().partition(" ")
            u.first_name, u.last_name = first[:150], last[:150]
        if "email" in d:
            u.email = d["email"]
        u.save(update_fields=["first_name", "last_name", "email"])
        if "preferences" in d:
            profile, _ = Profile.objects.get_or_create(user=u)
            allowed = {k: bool(v) for k, v in d["preferences"].items() if k in Profile.DEFAULTS}
            profile.preferences = {**(profile.preferences or {}), **allowed}
            profile.save(update_fields=["preferences"])
    return Response(me_data(u))


@api_view(["POST"])
def logout(request):
    """Hapus token di server. Token DRF satu per user, jadi semua perangkat user ini ikut keluar."""
    Token.objects.filter(user=request.user).delete()
    return Response({"ok": True})


@api_view(["POST"])
def change_password(request):
    s = PasswordChangeSerializer(data=request.data, context={"user": request.user})
    s.is_valid(raise_exception=True)
    u = request.user
    u.set_password(s.validated_data["new_password"])
    u.save(update_fields=["password"])
    Token.objects.filter(user=u).delete()  # perangkat lain wajib login ulang
    return Response({"token": Token.objects.create(user=u).key})


@api_view(["GET"])
def stats(request):
    outlets = visible_outlets(request.user)
    since = timezone.now() - WINDOW

    msgs = Message.objects.filter(
        conversation__outlet__in=outlets,
        created_at__gte=since,
    )

    unread = (
        Conversation.objects
        .filter(outlet__in=outlets)
        .aggregate(
            n=Coalesce(
                Sum("unread_count"),
                0,
            )
        )["n"]
    )

    contacts = (
        Contact.objects
        .filter(
            conversations__outlet__in=outlets
        )
        .distinct()
        .count()
    )

    return Response({
        "outlets": outlets.count(),
        "outlets_active": outlets.filter(
            is_active=True
        ).count(),
        "inbound_24h": msgs.filter(
            direction=Message.Direction.IN
        ).count(),
        "outbound_24h": msgs.filter(
            direction=Message.Direction.OUT
        ).count(),
        "unread": unread,
        "contacts": contacts,
    })


class OutletViewSet(viewsets.ModelViewSet):
    """Baca: semua user (sesuai akses). Tambah/ubah/hapus dan hubungkan nomor: khusus staf."""
    serializer_class = OutletSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        return [IsAuthenticated()] if self.action in ("list", "retrieve") else [IsAdminUser()]

    def get_queryset(self):
        return (visible_outlets(self.request.user).select_related("account")
                .annotate(unread=Coalesce(Sum("conversations__unread_count"), 0)))

    def destroy(self, request, *a, **kw):
        outlet = self.get_object()
        if outlet.conversations.exists():
            return Response({"detail": "Outlet masih punya percakapan. Nonaktifkan saja."}, status=409)
        return super().destroy(request, *a, **kw)

    @action(detail=False, methods=["post"], url_path="embedded-signup")
    def embedded_signup(self, request):
        """Dipanggil frontend setelah FB.login selesai. `code` hanya berlaku ~30 detik."""
        s = EmbeddedSignupSerializer(data=request.data)
        if not s.is_valid():
            log.warning("Embedded signup ditolak: %s (field terkirim: %s)", s.errors, sorted(request.data.keys()))
            return Response(s.errors, status=400)
        d = s.validated_data
        outlet = get_object_or_404(Outlet, pk=d["outlet"]) if d.get("outlet") else None
        try:
            outlet, warnings = onboard(code=d["code"], waba_id=d["waba_id"], phone_number_id=d["phone_number_id"],
                                       business_id=d["business_id"], event=d["event"], outlet=outlet,
                                       name=d["name"], city=d["city"])
        except whatsapp.WhatsAppError as e:
            log.warning("Embedded signup gagal: %s", e)
            return Response({"detail": str(e)}, status=502)
        outlet = self.get_queryset().get(pk=outlet.pk)
        return Response({"outlet": OutletSerializer(outlet).data, "warnings": warnings}, status=201)

    @action(detail=True, methods=["post"])
    def sync(self, request, pk=None):
        """Ulangi permintaan sinkron kontak/riwayat HP yang gagal (batas 24 jam setelah onboarding)."""
        outlet = self.get_object()
        if outlet.mode != Outlet.Mode.COEXIST:
            return Response({"detail": "Outlet ini tidak terhubung dengan aplikasi WhatsApp Business."}, status=400)
        if outlet.sync_blocked:
            return Response({"detail": outlet.sync_blocked + " Untuk mengulang: putuskan nomor di HP "
                             "(WhatsApp Business > Pengaturan > Akun > Business Platform), lalu Hubungkan ulang."}, status=409)
        errors = request_coexist_sync(outlet)
        body = {"errors": errors, "outlet": OutletSerializer(self.get_queryset().get(pk=outlet.pk)).data}
        if errors:
            body["detail"] = " | ".join(errors)  # ditampilkan apa adanya oleh frontend
        return Response(body, status=502 if errors else 200)


class ConversationViewSet(viewsets.ModelViewSet):
    serializer_class = ConversationSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        last = Message.objects.filter(conversation=OuterRef("pk")).order_by("-created_at")
        qs = (Conversation.objects.filter(outlet__in=visible_outlets(self.request.user))
              .select_related("outlet", "contact")
              .annotate(preview=Subquery(last.values("body")[:1]), preview_direction=Subquery(last.values("direction")[:1])))
        if self.action != "list":
            return qs
        p = self.request.query_params
        if p.get("outlet"):
            qs = qs.filter(outlet_id=p["outlet"])
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("unread") == "1":
            qs = qs.filter(unread_count__gt=0)
        if p.get("replied") == "1":
            qs = qs.filter(preview_direction="out")
        if p.get("assigned") == "me":
            qs = qs.filter(assigned_to=self.request.user)
        if p.get("updated_after") and (dt := parse_datetime(p["updated_after"])):
            qs = qs.filter(updated_at__gt=dt)  # untuk polling
        if p.get("q"):
            q = p["q"]
            qs = qs.filter(Q(contact__name__icontains=q) | Q(contact__wa_id__icontains=q) | Q(messages__body__icontains=q)).distinct()
        return qs

    def perform_update(self, serializer):
        conv = serializer.save()
        touch(conv.pk, conv.outlet_id, "update")

    def list(self, request, *a, **kw):
        qs = self.filter_queryset(self.get_queryset()).order_by(F("last_message_at").desc(nulls_last=True))[:LIMIT]
        return Response(self.get_serializer(qs, many=True).data)

    def create(self, request, *a, **kw):
        """Mulai percakapan baru dengan sebuah kontak (get-or-create)."""
        s = StartConversationSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        outlet = get_object_or_404(visible_outlets(request.user), pk=d["outlet"])
        contact, _ = Contact.objects.get_or_create(wa_id=d["wa_id"], defaults={"name": d["name"]})
        conv, created = Conversation.objects.get_or_create(outlet=outlet, contact=contact)
        return Response(ConversationSerializer(conv).data, status=201 if created else 200)

    @action(detail=True, methods=["get", "post"], url_path="messages")
    def messages(self, request, pk=None):
        conv = self.get_object()
        if request.method == "GET":  # membuka percakapan = menandai terbaca
            if conv.unread_count:
                touch(conv.pk, conv.outlet_id, "read", unread_count=0)
            qs = conv.messages.select_related("conversation__contact", "conversation__outlet", "sent_by")
            return Response(MessageSerializer(qs, many=True).data)
        return self._send(request, conv)

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        conv = self.get_object()
        touch(conv.pk, conv.outlet_id, "read", unread_count=0)
        return Response({"ok": True})

    @action(detail=True, methods=["post"], url_path="media", parser_classes=[MultiPartParser, FormParser])
    def media(self, request, pk=None):
        """Kirim file (gambar/video/audio/dokumen) + caption opsional. Hanya dalam jendela 24 jam."""
        conv = self.get_object()
        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "Pilih file yang akan dikirim."}, status=400)
        caption = (request.data.get("caption") or "").strip()[:1024]
        name = os.path.basename(f.name or "file")[:255]
        mime = (f.content_type or "").split(";")[0].lower()
        if not whatsapp.media_kind(mime):  # browser kadang mengirim application/octet-stream untuk .docx dsb.
            mime = (mimetypes.guess_type(name)[0] or mime).lower()
        kind = whatsapp.media_kind(mime)
        if not kind:
            return Response({"detail": f"Jenis file ini tidak didukung WhatsApp ({mime or 'tidak dikenal'}). "
                                       "Gunakan JPG/PNG, MP4, MP3/OGG/AAC, PDF, Word, Excel, PowerPoint, atau TXT."}, status=400)
        limit = whatsapp.MEDIA_RULES[kind][1]
        if f.size > limit:
            return Response({"detail": f"File terlalu besar ({f.size / 1048576:.1f} MB). Batas WhatsApp untuk "
                                       f"{ {'image': 'gambar', 'video': 'video', 'audio': 'audio', 'document': 'dokumen'}[kind] } "
                                       f"adalah {limit // 1048576} MB."}, status=400)
        if not conv.window_open:
            return Response({"detail": "Jendela 24 jam berakhir. File hanya bisa dikirim setelah pelanggan membalas."}, status=409)

        outlet = conv.outlet
        msg = Message.objects.create(conversation=conv, direction=Message.Direction.OUT, type=kind,
                                     body=caption or f"[{kind}]", status=Message.Status.QUEUED, sent_by=request.user,
                                     mime_type=mime[:120], filename=name, file_size=f.size)
        try:
            media_id = whatsapp.upload_media(outlet.phone_number_id, f, name, mime, token=outlet.token)
            Message.objects.filter(pk=msg.pk).update(media_id=media_id)
            wamid = whatsapp.send_media(outlet.phone_number_id, conv.contact.wa_id, kind, media_id,
                                        caption=caption, filename=name, token=outlet.token)
        except whatsapp.WhatsAppError as e:
            Message.objects.filter(pk=msg.pk).update(status=Message.Status.FAILED, error_detail=str(e)[:255])
            touch(conv.pk, conv.outlet_id, "status")
            return Response({"detail": str(e), "message_id": msg.id}, status=502)
        Message.objects.filter(pk=msg.pk).update(wamid=wamid, status=Message.Status.SENT)
        touch(conv.pk, conv.outlet_id, "message", last_message_at=msg.created_at)
        msg.refresh_from_db()
        return Response(MessageSerializer(msg).data, status=201)

    def _send(self, request, conv):
        text = (request.data.get("text") or "").strip()
        tpl_id = request.data.get("template")

        pnid = conv.outlet.phone_number_id
        to = conv.contact.wa_id

        if tpl_id:
            tpl = MessageTemplate.objects.filter(
                pk=tpl_id,
                status="approved",
                waba_id__in=template_wabas(conv.outlet),
            ).first()

            if not tpl:
                return Response(
                    {
                        "detail": (
                            "Template tidak ditemukan "
                            "atau belum disetujui."
                        )
                    },
                    status=400,
                )

            body = tpl.body
            mtype = "template"
            is_template = True

        elif text:
            if not conv.window_open:
                return Response(
                    {
                        "detail": (
                            "Jendela 24 jam berakhir. "
                            "Gunakan template."
                        )
                    },
                    status=409,
                )

            body = text
            mtype = "text"
            is_template = False

        else:
            return Response(
                {"detail": "Isi 'text' atau 'template'."},
                status=400,
            )

        # Buat record lokal dahulu.
        msg = Message.objects.create(
            conversation=conv,
            direction=Message.Direction.OUT,
            type=mtype,
            body=body,
            status=Message.Status.QUEUED,
            is_template=is_template,
            sent_by=request.user,
        )

        try:
            if is_template:
                wamid = whatsapp.send_template(
                    pnid,
                    to,
                    tpl.name,
                    tpl.language,
                    token=conv.outlet.token,
                )
            else:
                wamid = whatsapp.send_text(
                    pnid,
                    to,
                    text,
                    token=conv.outlet.token,
                )

        except whatsapp.WhatsAppError as e:
            Message.objects.filter(pk=msg.pk).update(
                status=Message.Status.FAILED,
                error_detail=str(e)[:255],
            )
            touch(conv.pk, conv.outlet_id, "status")

            return Response(
                {
                    "detail": str(e),
                    "message_id": msg.id,
                },
                status=502,
            )

        Message.objects.filter(pk=msg.pk).update(
            wamid=wamid,
            status=Message.Status.SENT,
        )

        touch(conv.pk, conv.outlet_id, "message", last_message_at=msg.created_at)

        msg.refresh_from_db()

        return Response(
            MessageSerializer(msg).data,
            status=201,
        )


class ContactViewSet(viewsets.ModelViewSet):
    serializer_class = ContactSerializer
    http_method_names = [
        "get",
        "post",
        "delete",
        "head",
        "options",
    ]

    def get_permissions(self):
        return (
            [IsAdminUser()]
            if self.action == "destroy"
            else [IsAuthenticated()]
        )

    def get_queryset(self):
        outlets = visible_outlets(self.request.user)

        qs = (
            Contact.objects
            .filter(conversations__outlet__in=outlets)
            .distinct()
            .order_by("-created_at")
        )

        q = self.request.query_params.get("q")

        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(wa_id__icontains=q)
            )

        return qs

    def destroy(self, request, *a, **kw):
        try:
            return super().destroy(request, *a, **kw)
        except ProtectedError:
            return Response(
                {
                    "detail": (
                        "Kontak masih punya riwayat percakapan."
                    )
                },
                status=409,
            )


class MessageViewSet(viewsets.ReadOnlyModelViewSet):
    """Daftar pesan lintas percakapan (untuk halaman Pesan Terkirim)."""
    serializer_class = MessageSerializer

    def get_queryset(self):
        qs = (Message.objects.filter(conversation__outlet__in=visible_outlets(self.request.user))
              .select_related("conversation__contact", "conversation__outlet", "sent_by").order_by("-created_at"))
        p = self.request.query_params
        if p.get("direction") in ("in", "out"):
            qs = qs.filter(direction=p["direction"])
        if p.get("template") in ("0", "1"):
            qs = qs.filter(is_template=p["template"] == "1")
        if p.get("q"):
            qs = qs.filter(Q(body__icontains=p["q"]) | Q(conversation__contact__name__icontains=p["q"]))
        return qs

    def list(self, request, *a, **kw):
        return Response(self.get_serializer(self.get_queryset()[:LIMIT], many=True).data)


def template_wabas(outlet):
    """Template milik WABA outlet. Data lama (waba_id kosong) dianggap milik WABA default .env."""
    w = outlet.waba_id
    return [w, ""] if w == settings.WA_WABA_ID else [w]


class TemplateViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TemplateSerializer

    def get_queryset(self):
        qs = MessageTemplate.objects.order_by("name")
        outlet_id = self.request.query_params.get("outlet")
        if outlet_id:
            outlet = get_object_or_404(visible_outlets(self.request.user), pk=outlet_id)
            qs = qs.filter(waba_id__in=template_wabas(outlet))
        return qs

    def get_permissions(self):
        return [IsAdminUser()] if self.action == "create" else super().get_permissions()

    def create(self, request, *a, **kw):
        """Ajukan template baru ke Meta (WABA outlet terpilih, atau WABA default) lalu simpan lokal."""
        s = TemplateCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        outlet = get_object_or_404(Outlet, pk=d["outlet"]) if d.get("outlet") else None
        waba_id = outlet.waba_id if outlet else settings.WA_WABA_ID
        token = outlet.token if outlet else None
        try:
            res = whatsapp.create_template(d["name"], d["language"], d["category"], d["body"], waba_id, token)
        except whatsapp.WhatsAppError as e:
            return Response({"detail": str(e)}, status=502)
        tpl, _ = MessageTemplate.objects.update_or_create(
            waba_id=waba_id, name=d["name"], language=d["language"],
            defaults={"category": d["category"], "body": d["body"],
                      "status": str(res.get("status", "PENDING")).lower()},
        )
        return Response(TemplateSerializer(tpl).data, status=201)

    @action(detail=False, methods=["post"], permission_classes=[IsAdminUser])
    def sync(self, request):
        try:
            return Response({"synced": sync_templates()})
        except whatsapp.WhatsAppError as e:
            return Response({"detail": str(e)}, status=502)
