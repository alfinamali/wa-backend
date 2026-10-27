from urllib.parse import urlparse

from django import template

register = template.Library()


@register.filter
def remove_domain(url):
    if not url:
        return ""

    parsed = urlparse(url)

    # hasil: /media/thumbnails/produk/file.jpg
    return parsed.path
