import re

from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Contact, Conversation, Message, MessageTemplate, Outlet


def digits(v):
    d = re.sub(r"\D", "", v or "")
    if len(d) < 8:
        raise serializers.ValidationError("Nomor tidak valid. Gunakan format internasional, mis. 62812xxxx.")
    return d


class OutletSerializer(serializers.ModelSerializer):
    unread = serializers.IntegerField(read_only=True, default=0)
    waba_id = serializers.CharField(read_only=True)
    members = serializers.PrimaryKeyRelatedField(many=True, required=False,
                                                 queryset=get_user_model().objects.all())

    class Meta:
        model = Outlet
        fields = ["id", "name", "city", "display_number", "phone_number_id", "is_active", "unread", "waba_id",
                  "mode", "verified_name", "connected_at", "contacts_sync_requested_at", "history_sync_requested_at",
                  "history_progress", "disconnected_reason", "members", "auto_reply", "auto_reply_text"]
        read_only_fields = ["mode", "verified_name", "connected_at", "contacts_sync_requested_at",
                            "history_sync_requested_at", "history_progress", "disconnected_reason"]


class ProfileUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=300, allow_blank=True)
    email = serializers.EmailField(allow_blank=True)
    preferences = serializers.DictField()


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField()
    new_password = serializers.CharField(min_length=8)

    def validate_old_password(self, v):
        if not self.context["user"].check_password(v):
            raise serializers.ValidationError("Password lama salah.")
        return v

    def validate_new_password(self, v):
        from django.contrib.auth.password_validation import validate_password
        validate_password(v, self.context["user"])
        return v


class EmbeddedSignupSerializer(serializers.Serializer):
    """Data dari FB.login (code) + event WA_EMBEDDED_SIGNUP (waba_id, phone_number_id, business_id)."""
    code = serializers.CharField(error_messages={
        "required": "code dari Meta kosong. Ulangi dari tombol Hubungkan (code hanya berlaku ~30 detik).",
        "blank": "code dari Meta kosong. Ulangi dari tombol Hubungkan (code hanya berlaku ~30 detik)."})
    waba_id = serializers.RegexField(r"^\d*$", required=False, allow_blank=True, default="")  # kosong -> dari token
    # event FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING bisa hanya membawa waba_id -> dicari di backend
    phone_number_id = serializers.RegexField(r"^\d+$", required=False, allow_blank=True, default="")
    business_id = serializers.CharField(required=False, allow_blank=True, default="")
    event = serializers.CharField(required=False, allow_blank=True, default="")
    outlet = serializers.IntegerField(required=False, allow_null=True)  # isi untuk menghubungkan ke outlet yg ada
    name = serializers.CharField(required=False, allow_blank=True, default="", max_length=120)
    city = serializers.CharField(required=False, allow_blank=True, default="", max_length=80)


class ConversationSerializer(serializers.ModelSerializer):
    outlet_name = serializers.CharField(source="outlet.name", read_only=True)
    name = serializers.CharField(source="contact.name", read_only=True)
    wa_id = serializers.CharField(source="contact.wa_id", read_only=True)
    window_open = serializers.BooleanField(read_only=True)
    window_expires_at = serializers.DateTimeField(read_only=True)
    preview = serializers.CharField(read_only=True, allow_null=True, default=None)
    preview_direction = serializers.CharField(read_only=True, allow_null=True, default=None)

    class Meta:
        model = Conversation
        fields = ["id", "outlet", "outlet_name", "name", "wa_id", "status", "assigned_to", "unread_count",
                  "last_message_at", "window_open", "window_expires_at", "preview", "preview_direction", "updated_at"]
        read_only_fields = [f for f in fields if f not in ("status", "assigned_to")]


class StartConversationSerializer(serializers.Serializer):
    outlet = serializers.IntegerField()
    wa_id = serializers.CharField()
    name = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_wa_id(self, v):
        return digits(v)


class MessageSerializer(serializers.ModelSerializer):
    contact_name = serializers.CharField(source="conversation.contact.name", read_only=True)
    outlet_name = serializers.CharField(source="conversation.outlet.name", read_only=True)
    sent_by_name = serializers.CharField(source="sent_by.username", read_only=True, default=None)

    class Meta:
        model = Message
        fields = ["id", "conversation", "direction", "type", "body", "status", "is_template", "source", "error_detail",
                  "created_at", "contact_name", "outlet_name", "sent_by_name"]


class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = ["id", "name", "wa_id"]

    def validate_wa_id(self, v):
        return digits(v)


class TemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageTemplate
        fields = ["id", "waba_id", "name", "language", "category", "status", "body"]


class TemplateCreateSerializer(serializers.Serializer):
    name = serializers.RegexField(
        r"^[a-z0-9_]{1,100}$",
        error_messages={"invalid": "Nama hanya boleh huruf kecil, angka, dan garis bawah (_)."},
    )
    language = serializers.CharField(default="id", max_length=10)
    category = serializers.ChoiceField(choices=["UTILITY", "MARKETING"])
    body = serializers.CharField(max_length=1024)
    outlet = serializers.IntegerField(required=False, allow_null=True)  # template diajukan ke WABA outlet ini

    def validate_body(self, v):
        v = v.strip()
        if "{{" in v:
            # send_template() saat ini tidak mengirim parameter, jadi template berisi variabel tidak bisa terkirim
            raise serializers.ValidationError("Variabel {{1}} belum didukung. Tulis isi pesan tanpa variabel.")
        return v
