from django.contrib import admin

from .models import Contact, Conversation, Message, MessageTemplate, Outlet, Profile, WhatsAppAccount


@admin.register(Outlet)
class OutletAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "display_number", "phone_number_id", "mode", "account", "is_active")
    list_filter = ("mode", "is_active")
    search_fields = ("name", "phone_number_id")
    filter_horizontal = ("members",)


@admin.register(WhatsAppAccount)
class WhatsAppAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "waba_id", "business_id", "created_at")
    exclude = ("access_token",)  # token tidak ditampilkan


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("name", "wa_id", "created_at")
    search_fields = ("name", "wa_id")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("contact", "outlet", "status", "assigned_to", "unread_count", "last_message_at")
    list_filter = ("outlet", "status")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "conversation", "direction", "source", "type", "status", "is_template")
    list_filter = ("direction", "source", "status", "is_template")
    search_fields = ("body", "wamid")


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "language", "category", "status", "waba_id")


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "preferences")
