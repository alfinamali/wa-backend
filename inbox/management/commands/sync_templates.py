from django.core.management.base import BaseCommand

from inbox.services import sync_templates


class Command(BaseCommand):
    help = "Sinkronkan template pesan dari WhatsApp Business Account (butuh WA_WABA_ID)."

    def handle(self, *args, **opts):
        self.stdout.write(f"{sync_templates()} template disinkronkan")
