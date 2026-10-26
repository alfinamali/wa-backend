from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

WINDOW = timedelta(hours=24)  # jendela layanan pelanggan WhatsApp


class WhatsAppAccount(models.Model):
    """Satu WABA milik pelanggan/outlet yang terhubung lewat Embedded Signup.

    Token di sini adalah business token hasil tukar `code` dari FB.login. Outlet tanpa akun
    memakai WA_TOKEN dan WA_WABA_ID dari .env (perilaku lama).
    """
    waba_id = models.CharField(max_length=64, unique=True)
    business_id = models.CharField(max_length=64, blank=True)
    name = models.CharField(max_length=160, blank=True)
    access_token = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or self.waba_id


class Outlet(models.Model):
    class Mode(models.TextChoices):
        CLOUD = "cloud", "Cloud API saja"
        COEXIST = "coexist", "Aplikasi HP + Cloud API (coexistence)"

    name = models.CharField(max_length=120)
    city = models.CharField(max_length=80, blank=True)
    phone_number_id = models.CharField(max_length=64, unique=True)  # dari Meta; routing webhook
    display_number = models.CharField(max_length=32)
    is_active = models.BooleanField(default=True)
    account = models.ForeignKey(WhatsAppAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name="outlets")
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.CLOUD)
    verified_name = models.CharField(max_length=160, blank=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    contacts_sync_requested_at = models.DateTimeField(null=True, blank=True)
    history_sync_requested_at = models.DateTimeField(null=True, blank=True)
    history_progress = models.PositiveSmallIntegerField(default=0)  # 0-100 dari webhook history
    disconnected_reason = models.CharField(max_length=120, blank=True)  # diisi saat PARTNER_REMOVED
    # Balasan otomatis. Pengganti pesan salam/away di aplikasi HP yang nonaktif saat coexistence.
    auto_reply = models.BooleanField(default=False)
    auto_reply_text = models.TextField(blank=True, default="Terima kasih sudah menghubungi kami. Tim kami akan segera membalas.")
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="outlets")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def token(self):
        return (self.account.access_token if self.account and self.account.access_token else settings.WA_TOKEN)

    @property
    def waba_id(self):
        return self.account.waba_id if self.account else settings.WA_WABA_ID

    @property
    def number_digits(self):
        return "".join(ch for ch in self.display_number if ch.isdigit())


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

    class Source(models.TextChoices):
        DASHBOARD = "dashboard", "Dashboard"
        CUSTOMER = "customer", "Pelanggan"
        PHONE = "phone", "Aplikasi HP"      # echo dari WhatsApp Business App (coexistence)
        HISTORY = "history", "Riwayat HP"   # hasil sinkron riwayat 180 hari
        AUTO = "auto", "Balasan otomatis"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    wamid = models.CharField(max_length=128, unique=True, null=True, blank=True)  # anti duplikat webhook
    direction = models.CharField(max_length=3, choices=Direction.choices)
    type = models.CharField(max_length=20, default="text")
    body = models.TextField(blank=True)
    media_id = models.CharField(max_length=128, blank=True)  # unduh lewat API; URL Meta cepat kedaluwarsa
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SENT)
    error_detail = models.CharField(max_length=255, blank=True)
    is_template = models.BooleanField(default=False)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.DASHBOARD)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["conversation", "created_at"])]


class MessageTemplate(models.Model):
    waba_id = models.CharField(max_length=64, blank=True, default="")  # template milik WABA tertentu
    name = models.CharField(max_length=120)
    language = models.CharField(max_length=10, default="id")
    category = models.CharField(max_length=20, blank=True)  # MARKETING / UTILITY / AUTHENTICATION
    status = models.CharField(max_length=12, default="pending")  # approved / pending / rejected
    body = models.TextField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["waba_id", "name", "language"], name="uniq_template_waba")]

    def __str__(self):
        return f"{self.name} ({self.language})"


class Profile(models.Model):
    """Preferensi per user (notifikasi, suara). Dibuat otomatis saat pertama dibaca."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    preferences = models.JSONField(default=dict, blank=True)

    DEFAULTS = {"notif": True, "sound": True}

    def prefs(self):
        return {**self.DEFAULTS, **(self.preferences or {})}
