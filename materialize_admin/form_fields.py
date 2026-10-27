from decimal import Decimal

from django import forms


class RupiahInput(forms.TextInput):
    def __init__(self, attrs=None):
        if attrs is None:
            attrs = {}
        # Hanya tambahkan class khusus rupiah
        attrs["class"] = attrs.get("class", "") + " display-rupiah"
        attrs["class"] = attrs["class"].strip()
        super().__init__(attrs=attrs)

    def format_value(self, value):
        if value == "" or value is None:
            return ""
        try:
            if isinstance(value, str):
                value = value.replace(".", "").replace(",", "")
            num_value = int(float(value))
            return "{:,}".format(num_value).replace(",", ".")
        except (ValueError, AttributeError, TypeError):
            return value


class RupiahField(forms.DecimalField):
    def __init__(self, *args, **kwargs):
        if "widget" not in kwargs:
            kwargs["widget"] = RupiahInput()
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, str):
            value = value.replace(".", "")
        return Decimal(value)


# Export the classes for use in other modules
__all__ = ["RupiahInput", "RupiahField"]
