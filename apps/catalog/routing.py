"""مساعدات مسارات المنتجات داخل الغلاف الرئيسي مقابل المسارات الكلاسيكية."""


def catalog_url_namespace(request) -> str:
    ns = (getattr(request.resolver_match, "namespace", None) or "").strip()
    return "shell" if ns == "shell" else "catalog"
