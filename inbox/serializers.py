import re

from rest_framework import serializers

from .models import Contact, Conversation, Message, MessageTemplate, Outlet


def digits(v):
    d = re.sub(r"\D", "", v or "")
    if len(d) < 8:
        raise serializers.ValidationError("Nomor tidak valid. Gunakan format internasional, mis. 62812xxxx.")
    return d


class OutletSerializer(serializers.ModelSerializer):
    unread = serializers.IntegerField(read_only=True)

    class Meta:
        model = Outlet
        fields = ["id", "name", "city", "display_number", "phone_number_id", "is_active", "unread"]


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
        fields = ["id", "conversation", "direction", "type", "body", "status", "is_template", "error_detail",
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
        fields = ["id", "name", "language", "category", "status", "body"]


class TemplateCreateSerializer(serializers.Serializer):
    name = serializers.RegexField(
        r"^[a-z0-9_]{1,100}$",
        error_messages={"invalid": "Nama hanya boleh huruf kecil, angka, dan garis bawah (_)."},
    )
    language = serializers.CharField(default="id", max_length=10)
    category = serializers.ChoiceField(choices=["UTILITY", "MARKETING"])
    body = serializers.CharField(max_length=1024)
 
    def validate_body(self, v):
        v = v.strip()
        if "{{" in v:
            # send_template() saat ini tidak mengirim parameter, jadi template berisi variabel tidak bisa terkirim
            raise serializers.ValidationError("Variabel {{1}} belum didukung. Tulis isi pesan tanpa variabel.")
        return v
