from django.contrib import admin

from .models import Contact, Conversation, Message, MessageTemplate, Outlet


@admin.register(Outlet)
class OutletAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "display_number", "phone_number_id", "is_active")
    search_fields = ("name", "phone_number_id")
    filter_horizontal = ("members",)


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
    list_display = ("created_at", "conversation", "direction", "type", "status", "is_template")
    list_filter = ("direction", "status", "is_template")
    search_fields = ("body", "wamid")


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "language", "category", "status")
