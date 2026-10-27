"""Menu sidebar /admin/ (template materialize_admin). Dipanggil per request lewat tag `get_menus`.

Item tampil hanya bila user punya izin melihat model tsb. Ikon: Remix Icon (kelas `ri ri-...`).
"""
from django.urls import NoReverseMatch, reverse

MENU = [
    {"title": "Beranda", "url": "admin:index", "icon": "ri ri-home-smile-line"},
    {"header": True, "title": "WhatsApp"},
    {"title": "Outlet", "model": "inbox.outlet", "icon": "ri ri-store-2-line"},
    {"title": "Akun WhatsApp", "model": "inbox.whatsappaccount", "icon": "ri ri-whatsapp-line"},
    {"title": "Percakapan", "model": "inbox.conversation", "icon": "ri ri-chat-3-line"},
    {"title": "Pesan", "model": "inbox.message", "icon": "ri ri-message-2-line"},
    {"title": "Kontak", "model": "inbox.contact", "icon": "ri ri-contacts-book-line"},
    {"title": "Template pesan", "model": "inbox.messagetemplate", "icon": "ri ri-file-text-line"},
    {"header": True, "title": "Pengguna"},
    {"title": "User", "model": "auth.user", "icon": "ri ri-user-line"},
    {"title": "Grup & izin", "model": "auth.group", "icon": "ri ri-team-line"},
    {"title": "Profil & preferensi", "model": "inbox.profile", "icon": "ri ri-user-settings-line"},
    {"title": "Token API", "model": "authtoken.tokenproxy", "icon": "ri ri-key-2-line"},
]


def get_menu_items(user):
    items, pending_header = [], None
    for m in MENU:
        if m.get("header"):
            pending_header = {"header": True, "title": m["title"]}
            continue
        if "model" in m:
            app, model = m["model"].split(".")
            if not (user and user.is_active and user.has_perm(f"{app}.view_{model}")):
                continue
            name = f"admin:{app}_{model}_changelist"
        else:
            name = m["url"]
        try:
            url = reverse(name)
        except NoReverseMatch:
            continue
        if pending_header:  # header hanya tampil bila ada item di bawahnya
            items.append(pending_header)
            pending_header = None
        items.append({"title": m["title"], "url": url, "icon": m["icon"]})
    return items
