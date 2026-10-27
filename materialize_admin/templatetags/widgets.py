from django.contrib.admin.widgets import ForeignKeyRawIdWidget
from django.contrib.auth.forms import ReadOnlyPasswordHashWidget
from django.forms import (CharField, CheckboxInput, DateInput, DecimalField,
                          FileInput, RadioSelect, Select, SplitDateTimeWidget,
                          Textarea, TimeInput)
from django.forms.widgets import CheckboxSelectMultiple, Widget
from django.template import Library
from django.utils.html import format_html

from materialize_admin.form_fields import RupiahInput

# Catatan: versi backend-pusat juga punya filter produk (gudang.*). Tidak dipakai di proyek ini.

register = Library()


@register.filter
def classname(value):
    return value.__class__.__name__


# @register.filter(name="addcls")
# def addcls(field, css):
#     if hasattr(field, "as_widget"):
#         return field.as_widget(attrs={"class": css})
#     return None


@register.filter(name="addcls")
def addcls(field, css):
    if hasattr(field, "as_widget"):
        existing = field.field.widget.attrs.get("class", "")
        field.field.widget.attrs["class"] = (existing + " " + css).strip()
        return field
    return None


@register.filter(name="atribut")
def atribut(field_, attr_):
    if hasattr(field_, "as_widget"):
        attrs = {}
        attrs_from_str = attr_.split("|")
        for attr in attrs_from_str:
            k_, v_ = attr.split(":")
            attrs.update({k_: v_})
        return field_.as_widget(attrs=attrs)
    return None


@register.filter
def widget_attr(field, name):
    try:
        return field.field.widget.attrs.get(name)
    except Exception:
        return None


@register.filter
def has_widget_class(field, cls):
    classes = field.field.widget.attrs.get("class", "")
    return cls in classes.split()


@register.filter("is_select")
def is_select(field):
    if isinstance(field.field.widget, RadioSelect) or isinstance(
        field.field.widget, CheckboxSelectMultiple
    ):
        return False
    name = str(field.field.widget.__class__.__name__)
    isinstance_ = isinstance(field.field.widget, Select)
    return isinstance_ or name == "RelatedFieldWidgetWrapper"


@register.filter("is_radio")
def is_radio(field):
    return isinstance(field.field.widget, RadioSelect)


@register.filter("is_decimal")
def is_decimal(field):
    return isinstance(field.field, DecimalField)


@register.filter("is_date")
def is_date(field):
    return isinstance(field.field.widget, DateInput)


@register.filter("is_datetime")
def is_datetime(field):
    return isinstance(field.field.widget, SplitDateTimeWidget)


@register.filter("is_time")
def is_time(field):
    return isinstance(field.field.widget, TimeInput)


@register.filter("is_file")
def is_file(field):
    return isinstance(field.field.widget, FileInput)


@register.filter("is_char")
def is_char(field):
    return isinstance(field.field.widget, CharField)


@register.filter("is_textarea")
def is_textarea(field):
    return isinstance(field.field.widget, Textarea)


@register.filter("is_bool")
def is_bool(field):
    return isinstance(field.field.widget, CheckboxInput)


@register.filter("is_raw_id")
def is_raw_id(field):
    return isinstance(field.field.widget, ForeignKeyRawIdWidget)


@register.filter("is_readonlypassword")
def is_readonlypassword(field):
    return isinstance(field.field.widget, ReadOnlyPasswordHashWidget)


@register.filter("is_daftar_produk")
def is_daftar_produk(field):
    return False  # widget daftar produk khusus backend-pusat


@register.filter("is_rupiah_field")
def is_rupiah_field(field):
    return isinstance(field.field.widget, RupiahInput)


@register.filter(name="get_filter_choices")
def get_filter_choices(spec, cl):
    if spec:
        return list(spec.choices(cl))
    return ()


@register.filter(name="lookup")
def lookup(value, arg):
    return value[arg]


@register.filter("is_required")
def is_required(field):
    required = str(field).find("required")
    if required > 0:
        return True
    return False


@register.filter
def divide(value, arg):
    try:
        return int(value) / int(arg)
    except (ValueError, ZeroDivisionError):
        return None


@register.filter(name="replace")
def replace(value, _str):
    str_split = _str.split("|")
    before = str_split[0]
    after = str_split[1]
    return value.replace(before, after)


@register.filter(name="convert_to_url")
def convert_to_url(value):
    items = "&".join(f"{key}={value}" for key, value in value.items())
    return "?{}".format(items)


@register.filter
def is_type(field, field_type):
    return isinstance(field.field, getattr(field, field_type, None))


@register.filter
def is_widget(field, widget_name):
    return field.field.widget.__class__.__name__ == widget_name


@register.filter
def split(value, sep=","):
    if not value:
        return []
    return [v.strip() for v in str(value).split(sep) if v.strip()]


@register.filter("is_multiselect")
def is_multiselect(field):
    """Check if the field is a MultiSelectField"""
    # Check by class name since we might not have direct access to the MultiSelectField class
    return (
        "MultiSelectField" in str(field.field.__class__.__module__)
        or field.field.__class__.__name__ == "MultiSelectFormField"
        or hasattr(field.field, "choices")
        and isinstance(field.field.choices, list)
        and getattr(field.field, "max_length", None) is not None
    )


class RupiahReadonlyWidget(Widget):
    def render(self, name, value, attrs=None, renderer=None):
        if value is None or value == "":
            display = "-"
            hidden_value = ""
        else:
            try:
                from decimal import Decimal

                val = Decimal(str(value))
                formatted = (
                    f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                )
                display = f"Rp {formatted}"
                hidden_value = str(
                    val
                )  # ← kirim sebagai decimal valid, misal "1500000.00"
            except Exception:
                display = str(value)
                hidden_value = str(value)

        return format_html(
            '<div style="padding: 8px 0; color: #333;">{}</div>'
            '<input type="hidden" name="{}" value="{}">',
            display,
            name,
            hidden_value,
        )

    def value_from_datadict(self, data, files, name):
        return data.get(name)
