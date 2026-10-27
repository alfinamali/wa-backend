from django import template
from django.contrib.admin.templatetags.admin_modify import \
    submit_row as original_submit_row

register = template.Library()


@register.inclusion_tag("admin/materialize_submit_line.html", takes_context=True)
def materialize_submit_row(context):
    """
    Display the row of buttons for delete and save.
    Uses the custom materialize_submit_line.html template with Materialize styling.
    """
    ctx = original_submit_row(context)
    return ctx
