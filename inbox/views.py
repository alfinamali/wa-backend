import json
import logging

from django.conf import settings
from django.db.models import F, OuterRef, Q, Subquery, Sum
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from . import whatsapp
from .models import WINDOW, Contact, Conversation, Message, MessageTemplate, Outlet
from .serializers import (ContactSerializer, ConversationSerializer, MessageSerializer, OutletSerializer,
                          StartConversationSerializer, TemplateSerializer)
from .services import process_payload, sync_templates

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


# ---------------- API ----------------
@api_view(["GET"])
def me(request):
    u = request.user
    return Response({"id": u.id, "username": u.username, "name": u.get_full_name() or u.username, "is_staff": u.is_staff,
                     "outlets": list(visible_outlets(u).values_list("id", flat=True))})


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


class OutletViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = OutletSerializer

    def get_queryset(self):
        return visible_outlets(self.request.user).annotate(unread=Coalesce(Sum("conversations__unread_count"), 0))


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
                Conversation.objects.filter(pk=conv.pk).update(unread_count=0, updated_at=timezone.now())
            qs = conv.messages.select_related("conversation__contact", "conversation__outlet", "sent_by")
            return Response(MessageSerializer(qs, many=True).data)
        return self._send(request, conv)

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        conv = self.get_object()
        Conversation.objects.filter(pk=conv.pk).update(unread_count=0, updated_at=timezone.now())
        return Response({"ok": True})

    def _send(self, request, conv):
        text = (request.data.get("text") or "").strip()
        tpl_id = request.data.get("template")

        pnid = conv.outlet.phone_number_id
        to = conv.contact.wa_id

        if tpl_id:
            tpl = MessageTemplate.objects.filter(
                pk=tpl_id,
                status="approved",
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
                )
            else:
                wamid = whatsapp.send_text(
                    pnid,
                    to,
                    text,
                )

        except whatsapp.WhatsAppError as e:
            Message.objects.filter(pk=msg.pk).update(
                status=Message.Status.FAILED,
                error_detail=str(e)[:255],
            )

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

        Conversation.objects.filter(pk=conv.pk).update(
            last_message_at=msg.created_at,
            updated_at=timezone.now(),
        )

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


class TemplateViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TemplateSerializer
    queryset = MessageTemplate.objects.order_by("name")

    @action(detail=False, methods=["post"], permission_classes=[IsAdminUser])
    def sync(self, request):
        try:
            return Response({"synced": sync_templates()})
        except whatsapp.WhatsAppError as e:
            return Response({"detail": str(e)}, status=502)



class TemplateViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TemplateSerializer
    queryset = MessageTemplate.objects.order_by("name")
 
    def get_permissions(self):
        return [IsAdminUser()] if self.action == "create" else super().get_permissions()
 
    def create(self, request, *a, **kw):
        """Ajukan template baru ke Meta lalu simpan lokal (status awal pending)."""
        s = TemplateCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        try:
            res = whatsapp.create_template(d["name"], d["language"], d["category"], d["body"])
        except whatsapp.WhatsAppError as e:
            return Response({"detail": str(e)}, status=502)
        tpl, _ = MessageTemplate.objects.update_or_create(
            name=d["name"], language=d["language"],
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
