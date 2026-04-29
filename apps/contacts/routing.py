"""مساعدات مسارات العملاء: تمييز الغلاف (shell) عن المسار الكلاسيكي (contacts) لإعادة التوجيه والقوالب."""


def contacts_url_namespace(request) -> str:
    forced = getattr(request, "_contacts_effective_ns", None)
    if forced in ("shell", "contacts"):
        return forced
    ns = (getattr(request.resolver_match, "namespace", None) or "").strip()
    return "shell" if ns == "shell" else "contacts"
