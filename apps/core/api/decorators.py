from functools import wraps

from apps.core.api.responses import json_error


def api_login_required(view_func):
    """يتطلب مستخدماً مسجّلاً؛ يُرجع JSON 401 بدل إعادة توجيه HTML."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return json_error("يجب تسجيل الدخول", status=401, code="AUTH_REQUIRED")
        return view_func(request, *args, **kwargs)

    return _wrapped
