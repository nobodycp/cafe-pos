# Agent / contributor scope

## تعديلات الكود

- **يُمنع** تغيير أو إضافة سلوك (لوجيك، واجهة، إعدادات، قواعد بيانات، أو تبعيات) لم يُطلب **صراحةً** في الرسالة الحالية أو في قائمة مهام متفق عليها.
- إذا كان الإصلاح يقتضي تغييراً جانبياً، يُفضّل **شرحه في ردّ واحد** وانتظار الموافقة، أو تقييد التغيير لأقل نطاق ممكن.
- لا توسيع نطاق العمل «لتحسين المشروع» أو «للتناسق» دون طلب.

## حقول الإدخال النصية والاقتراحات

- أي حقل يُكتَب فيه **يدوياً** اسم كيان مرتبط ببيانات النظام (عميل، مورد، صنف، وحدة، محوّل، …) **يجب** أن يعرض **اقتراحات بحث** أثناء الكتابة من المصدر الصحيح (API/قائمة مرتبطة)، وليس حقل نص حر فقط.
- **عملاء POS/الآجل:** استخدم `CafeCustomerAutocomplete.bind` مع `pos:customers_search` (و`pos:customer_quick_create` عند عدم وجود نتائج) — نفس السلة وإتمام الدفع.
- **قوائم الاقتراحات (hits):** لا تعتمد على `style.display` فقط إذا كان العنصر يحمل صنف Tailwind `hidden` (`display:none !important`). إمّا `style="display:none"` بدون `hidden`، أو أزل/أعد `hidden` من JavaScript (انظر `static/js/customer_search_autocomplete.js`).
- عند إضافة حقل بحث جديد: حقل مخفي للمعرّف + حاوية `#…-hits` + ربط السكربت عند تحميل النموذج/النافذة المنبثقة (بعد حقن HTML عبر AJAX).

## جداول مطابقة الصناديق / التقارير المالية

- أي جدول مالي RTL (وارد/صادر/متبقي، مطابقة صناديق، ملخص وردية، …) يستخدم `#session-reconcile-table` أو `class="reconcile-table"` مع أعمدة `col-label` / `col-num`.
- `col-label` → `text-align: start !important`؛ `col-num` → `text-align: end !important` + `tabular-nums` + `white-space: nowrap`.
- ضمّن `{% include "includes/_reconcile_table_styles.html" %}` قبل الجدول (يتجاوز `.pos-shell-content th { text-align: start !important; }` في `app.css`).
- أزرار/روابط داخل `td.col-num`: غلّفها بـ `<span class="reconcile-num-inner">` — لا تستخدم `block w-full` وحدها للمحاذاة.

## Cursor

مجلد `.cursor/` مُستثنى من Git؛ لنسخ هذه القواعد إلى Cursor يمكن إنشاء قاعدة مطابقة تحت `.cursor/rules/` محلياً (مثلاً `autocomplete-inputs.mdc` بنفس فقرة «حقول الإدخال» أعلاه، أو `reconcile-tables.mdc` لجدول الصناديق).
