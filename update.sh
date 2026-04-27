#!/usr/bin/env bash
# ──────────────────────────────────────────────────
#  تحديث النظام من GitHub بأمر واحد
#  bash update.sh
# ──────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[*]${NC} $1"; }
ok()    { echo -e "${GREEN}[✓]${NC} $1"; }
fail()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

[ -f "manage.py" ] || fail "شغّل هذا السكريبت من داخل مجلد المشروع"

info "جاري سحب آخر التحديثات..."
git pull --ff-only || fail "فشل التحديث — تحقق من وجود تعديلات محلية"
ok "الكود محدّث"

source .venv/bin/activate 2>/dev/null || { info "تفعيل البيئة..."; source venv/bin/activate 2>/dev/null || source env/bin/activate; }

info "تحديث المكتبات..."
pip install -r requirements.txt -q
ok "المكتبات محدّثة"

info "تطبيق تغييرات قاعدة البيانات..."
python3 manage.py migrate -v 0
ok "قاعدة البيانات محدّثة"

info "تحديث الملفات الثابتة..."
python3 manage.py collectstatic --noinput -v 0 2>/dev/null || true
ok "الملفات الثابتة محدّثة"

echo ""
echo -e "${GREEN}════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✓ التحديث اكتمل — أعد تشغيل السيرفر${NC}"
echo -e "${GREEN}════════════════════════════════════════════${NC}"
echo ""
