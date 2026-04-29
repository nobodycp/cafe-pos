"""مساعدات مسارات المخزون داخل الغلاف الرئيسي مقابل المسارات الكلاسيكية."""


def inventory_url_namespace(request) -> str:
    ns = (getattr(request.resolver_match, "namespace", None) or "").strip()
    return "shell" if ns == "shell" else "inventory"
