from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

WINDOW = timedelta(hours=24)  # jendela layanan pelanggan WhatsApp


class Outlet(models.Model):
    name = models.CharField(max_length=120)
    city = models.CharField(max_length=80, blank=True)
    phone_number_id = models.CharField(max_length=64, unique=True)  # dari Meta; routing webhook
    display_number = models.CharField(max_length=32)
    is_active = models.BooleanField(default=True)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="outlets")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Contact(models.Model):
    wa_id = models.CharField(max_length=32, unique=True)  # hanya angka, mis. 62812...
    name = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name or self.wa_id


class Conversation(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PENDING = "pending", "Pending"
        RESOLVED = "resolved", "Resolved"

    outlet = models.ForeignKey(Outlet, on_delete=models.CASCADE, related_name="conversations")
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="conversations")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned")
    unread_count = models.PositiveIntegerField(default=0)
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_inbound_at = models.DateTimeField(null=True, blank=True)  # patokan jendela 24 jam
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["outlet", "contact"], name="uniq_outlet_contact")]
        indexes = [models.Index(fields=["outlet", "-last_message_at"]), models.Index(fields=["updated_at"])]

    @property
    def window_expires_at(self):
        return self.last_inbound_at + WINDOW if self.last_inbound_at else None

    @property
    def window_open(self):
        exp = self.window_expires_at
        return bool(exp and exp > timezone.now())

    def __str__(self):
        return f"{self.contact} @ {self.outlet}"


class Message(models.Model):
    class Direction(models.TextChoices):
        IN = "in", "Masuk"
        OUT = "out", "Keluar"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"
        FAILED = "failed", "Failed"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    wamid = models.CharField(max_length=128, unique=True, null=True, blank=True)  # anti duplikat webhook
    direction = models.CharField(max_length=3, choices=Direction.choices)
    type = models.CharField(max_length=20, default="text")
    body = models.TextField(blank=True)
    media_id = models.CharField(max_length=128, blank=True)  # unduh lewat API; URL Meta cepat kedaluwarsa
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SENT)
    error_detail = models.CharField(max_length=255, blank=True)
    is_template = models.BooleanField(default=False)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["conversation", "created_at"])]


class MessageTemplate(models.Model):
    name = models.CharField(max_length=120)
    language = models.CharField(max_length=10, default="id")
    category = models.CharField(max_length=20, blank=True)  # MARKETING / UTILITY / AUTHENTICATION
    status = models.CharField(max_length=12, default="pending")  # approved / pending / rejected
    body = models.TextField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["name", "language"], name="uniq_template")]

    def __str__(self):
        return f"{self.name} ({self.language})"
