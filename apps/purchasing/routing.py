"""مساعدات مسارات الموردين داخل الغلاف الرئيسي مقابل المسارات الكلاسيكية."""


def purchasing_url_namespace(request) -> str:
    ns = (getattr(request.resolver_match, "namespace", None) or "").strip()
    return "shell" if ns == "shell" else "purchasing"
