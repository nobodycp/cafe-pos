"""مساعدة النوافذ المنبثقة (لوحات AJAX) في غلاف التشغيل."""

from __future__ import annotations

from typing import Any, Callable, Optional

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string


def is_panel_embed(request: HttpRequest) -> bool:
    return (request.POST.get("panel_embed") or "").strip() == "1"


def is_panel_request(request: HttpRequest) -> bool:
    if is_panel_embed(request):
        return True
    return (request.headers.get("X-Requested-With") or "").lower() == "xmlhttprequest"


def panel_json_ok(
    request: HttpRequest,
    *,
    reload: bool = True,
    redirect: str = "",
    message: str = "",
) -> Optional[JsonResponse]:
    if not is_panel_request(request):
        return None
    return JsonResponse(
        {
            "ok": True,
            "reload": reload,
            "redirect": redirect,
            "message": message,
        }
    )


def panel_json_errors(
    request: HttpRequest,
    *,
    html: str,
    message: str = "",
    status: int = 400,
) -> Optional[JsonResponse]:
    if not is_panel_request(request):
        return None
    return JsonResponse(
        {
            "ok": False,
            "html": html,
            "error": message or "راجع البيانات",
        },
        status=status,
    )


def render_panel(
    request: HttpRequest,
    template_name: str,
    context: dict[str, Any] | None = None,
    *,
    wide: bool = False,
) -> HttpResponse:
    ctx = dict(context or {})
    ctx.setdefault("panel_wide", wide)
    return render(request, template_name, ctx)


def handle_panel_form(
    request: HttpRequest,
    *,
    template_name: str,
    build_context: Callable[[], dict[str, Any]],
    on_valid: Callable[[], HttpResponse],
    wide: bool = False,
) -> HttpResponse:
    """POST مع panel_embed: نجاح JSON أو إعادة عرض الأخطاء."""
    if request.method == "POST":
        try:
            result = on_valid()
        except PanelFormInvalid as exc:
            ctx = build_context()
            ctx["panel_form_errors"] = exc.message
            html = render_to_string(template_name, ctx, request=request)
            jr = panel_json_errors(request, html=html, message=exc.message)
            if jr:
                return jr
            return render_panel(request, template_name, ctx, wide=wide)
        if isinstance(result, JsonResponse):
            return result
        jr = panel_json_ok(request, reload=True, message=getattr(result, "panel_message", ""))
        if jr:
            return jr
        return result
    return render_panel(request, template_name, build_context(), wide=wide)


class PanelFormInvalid(Exception):
    def __init__(self, message: str = "راجع البيانات"):
        self.message = message
        super().__init__(message)


def panelize_form(form, *, extra_class: str = "ds-input w-full text-[11px]") -> None:
    """يضيف صنف الحقول المدمجة للوحة المنبثقة."""
    for field in form.fields.values():
        w = field.widget
        existing = (w.attrs.get("class") or "").strip()
        w.attrs["class"] = f"{extra_class} {existing}".strip()
