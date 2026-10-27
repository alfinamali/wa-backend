"""Realtime: dorong perubahan percakapan ke dashboard lewat Server-Sent Events (SSE).

Dua mode:
- REDIS_URL diisi  -> setiap perubahan dipublikasikan ke channel Redis dan langsung diteruskan ke semua
                       koneksi SSE di semua worker (instan). DB tetap dicek tiap 5 detik sebagai jaring pengaman.
- tanpa Redis       -> tiap koneksi SSE mengecek `Conversation.updated_at` di database setiap 1 detik.
                       Tetap terasa realtime tanpa infrastruktur tambahan, cocok untuk tim kecil.

Frontend hanya diberi tahu "percakapan X berubah"; datanya tetap diambil lewat API biasa
(`/api/conversations/?updated_after=...`), jadi aturan akses per outlet tetap satu sumber.
"""
import json
import logging
import time

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

log = logging.getLogger(__name__)
CHANNEL = "wa-events"
_redis = None
_redis_failed_at = 0.0


def redis_client():
    """Klien Redis bersama, atau None bila REDIS_URL kosong / Redis tidak bisa dipakai."""
    global _redis, _redis_failed_at
    url = getattr(settings, "REDIS_URL", "")
    if not url or (_redis_failed_at and time.monotonic() - _redis_failed_at < 30):
        return None
    if _redis is None:
        try:
            import redis
            _redis = redis.Redis.from_url(url, socket_connect_timeout=3, socket_timeout=5, health_check_interval=30)
        except Exception as e:  # paket belum terpasang / URL salah
            log.warning("Redis tidak bisa dipakai (%s); realtime memakai pengecekan database.", e)
            _redis_failed_at = time.monotonic()
            return None
    return _redis


def publish(conv_id, outlet_id, kind="change"):
    r = redis_client()
    if not r:
        return
    try:
        r.publish(CHANNEL, json.dumps({"conversation": conv_id, "outlet": outlet_id, "kind": kind}))
    except Exception as e:
        global _redis_failed_at
        _redis_failed_at = time.monotonic()
        log.warning("Gagal publish ke Redis: %s", e)


def touch(conv_id, outlet_id=None, kind="change", **fields):
    """Ubah percakapan (selalu menaikkan updated_at) lalu umumkan perubahannya setelah commit."""
    from .models import Conversation
    fields.setdefault("updated_at", timezone.now())
    Conversation.objects.filter(pk=conv_id).update(**fields)
    if outlet_id is None:
        outlet_id = Conversation.objects.filter(pk=conv_id).values_list("outlet_id", flat=True).first()
    transaction.on_commit(lambda: publish(conv_id, outlet_id, kind))


def _release():
    """Lepas koneksi DB di antara pengecekan (dilewati di dalam transaksi, mis. saat tes)."""
    if not connection.in_atomic_block:
        connection.close()  # noqa: satu-satunya tempat menutup koneksi


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def stream(user_is_staff, outlet_ids, seconds=None):
    """Generator SSE untuk satu koneksi. Ditutup setelah `seconds` detik; browser menyambung ulang otomatis.

    Koneksi dibatasi umurnya agar thread server dan koneksi DB tidak tertahan selamanya.
    """
    from .models import Conversation

    seconds = seconds or getattr(settings, "REALTIME_STREAM_SECONDS", 55)
    deadline = time.monotonic() + seconds
    allowed = set(outlet_ids)

    def scope(qs):
        return qs if user_is_staff else qs.filter(outlet_id__in=allowed)

    cursor = scope(Conversation.objects).order_by("-updated_at").values_list("updated_at", flat=True).first() or timezone.now()
    _release()  # jangan menahan koneksi DB selama stream menunggu

    r = redis_client()
    pubsub = None
    if r:
        try:
            pubsub = r.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(CHANNEL)
        except Exception as e:
            log.warning("Subscribe Redis gagal (%s); memakai pengecekan database.", e)
            pubsub = None

    yield "retry: 3000\n\n"
    yield _sse("hello", {"mode": "redis" if pubsub else "db"})
    db_every = 5.0 if pubsub else 1.0
    last_db = last_ping = time.monotonic()
    try:
        while time.monotonic() < deadline:
            if pubsub:
                try:
                    msg = pubsub.get_message(timeout=1.0)
                except Exception as e:
                    log.warning("Redis terputus di tengah stream: %s", e)
                    pubsub, db_every = None, 1.0
                    msg = None
                if msg:
                    try:
                        data = json.loads(msg["data"])
                    except (TypeError, ValueError):
                        data = None
                    if data and (user_is_staff or data.get("outlet") in allowed):
                        yield _sse("change", data)
            else:
                time.sleep(1.0)

            now = time.monotonic()
            if now - last_db >= db_every:
                last_db = now
                rows = list(scope(Conversation.objects.filter(updated_at__gt=cursor))
                            .values_list("id", "outlet_id", "updated_at")[:100])
                _release()
                if rows:
                    cursor = max(r_[2] for r_ in rows)
                    yield _sse("change", {"conversations": [r_[0] for r_ in rows], "kind": "db"})
            if now - last_ping >= 15:
                last_ping = now
                yield ": ping\n\n"  # komentar SSE, menjaga koneksi tetap hidup lewat proxy
    finally:
        if pubsub:
            try:
                pubsub.close()
            except Exception:
                pass
        _release()
