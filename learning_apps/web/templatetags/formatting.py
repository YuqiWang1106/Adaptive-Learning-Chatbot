from django import template

register = template.Library()


@register.filter
def underscore_to_space(value):
    """Handle underscore to space."""
    if value is None:
        return ""
    return str(value).replace("_", " ")


@register.filter
def list_item(value, index):
    """Return a list item by index for templates."""
    try:
        return value[int(index)]
    except (IndexError, TypeError, ValueError):
        return ""
