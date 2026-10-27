import calendar
from urllib.parse import parse_qs

from django import template

register = template.Library()


@register.filter(name="parseqs")
def parseqs(qs, key_):
    key_value = parse_qs(qs)
    if key_ in key_value:
        return parse_qs(qs)[key_][0]
    else:
        return ""


@register.filter(name="month")
def month(i_):
    if i_:
        month = calendar.month_abbr[i_]
        return month


# @register.filter(name='parameter_url')
# def parameter_url(query_dict):
# 	parameter = query_dict.copy()
# 	return parameter.urlencode()


@register.filter(name="parameter_set")
def parameter_set(query_dict, key_value):
    parameter = query_dict.copy()
    key, value = key_value.split(":")
    parameter.__setitem__(key, value)
    return parameter.urlencode()


@register.filter(name="set_parameter")
def set_parameter(query_dict, key_value):
    parameter = query_dict.copy()
    key, value = key_value.split(":")
    parameter.__setitem__(key, value)
    return parameter


@register.filter(name="pop_parameter")
def pop_parameter(query_dict_, key_):
    parameter = query_dict_.copy()
    parameter.__setitem__(key_, "0")
    parameter.pop(key_)
    return parameter


@register.filter(name="parameter_pop")
def parameter_pop(query_dict, key):
    parameter = query_dict.copy()
    parameter = pop_parameter(parameter, key)
    return parameter.urlencode()
