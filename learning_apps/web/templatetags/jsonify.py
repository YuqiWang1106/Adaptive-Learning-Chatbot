import json
from django import template
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def tojson(value):
    """Handle tojson."""
    return mark_safe(json.dumps(value, cls=DjangoJSONEncoder))
