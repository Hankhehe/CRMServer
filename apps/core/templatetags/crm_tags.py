from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag(takes_context=True)
def sort_link(context, field, label):
    request = context.get("request")
    if not request:
        return label

    current_order = request.GET.get("order", "")

    is_active = False
    is_desc = False

    if current_order == field:
        is_active = True
        next_order = f"-{field}"
    elif current_order == f"-{field}":
        is_active = True
        is_desc = True
        next_order = field
    else:
        next_order = field

    get_params = request.GET.copy()
    get_params["order"] = next_order
    if "page" in get_params:
        get_params["page"] = "1"

    query_string = get_params.urlencode()
    url = f"?{query_string}"

    if is_active:
        if is_desc:
            icon = '<i class="fa-solid fa-sort-down" style="margin-left: 4px; color: var(--primary);"></i>'
        else:
            icon = '<i class="fa-solid fa-sort-up" style="margin-left: 4px; color: var(--primary);"></i>'
    else:
        icon = '<i class="fa-solid fa-sort" style="margin-left: 4px; color: var(--text-muted); opacity: 0.4;"></i>'

    html = f'<a href="{url}" style="text-decoration: none; color: inherit; display: inline-flex; align-items: center; width: 100%;">{label}{icon}</a>'
    return mark_safe(html)


@register.simple_tag(takes_context=True)
def page_url(context, page_number):
    request = context.get("request")
    if not request:
        return f"?page={page_number}"
    get_params = request.GET.copy()
    get_params["page"] = page_number
    return f"?{get_params.urlencode()}"
