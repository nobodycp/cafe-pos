#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  نظام نقاط البيع — تثبيت إنتاجي بأمر واحد
#
#  على سيرفر العميل:
#    bash install.sh
#
#  يثبّت كل شيء + يشتغل تلقائياً حتى لو السيرفر أعاد تشغيل
# ══════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${CYAN}[*]${NC} $1"; }
ok()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
fail()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

REPO="https://github.com/nobodycp/cafe-pos.git"
APP_DIR="${APP_DIR:-$HOME/cafe-pos}"
PORT="${PORT:-8000}"
SERVICE_NAME="cafe-pos"

echo ""
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}     نظام نقاط البيع — تثبيت تلقائي كامل       ${NC}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════${NC}"
echo ""

# ── 1. Prerequisites ──
info "التحقق من المتطلبات..."
command -v python3 >/dev/null 2>&1 || fail "Python 3 غير مثبّت. ثبّته أولاً: sudo apt install python3 python3-venv python3-pip"
command -v git     >/dev/null 2>&1 || fail "Git غير مثبّت. ثبّته أولاً: sudo apt install git"

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 8 ]; }; then
    fail "Python 3.8+ مطلوب. الإصدار الحالي: $PY_VER"
fi
ok "Python $PY_VER"

# ── 2. Clone / Pull ──
if [ -d "$APP_DIR/.git" ]; then
    info "المجلد موجود — جاري التحديث..."
    cd "$APP_DIR"
    git pull --ff-only || { warn "تعارض — جاري إعادة التحميل..."; cd ..; rm -rf "$APP_DIR"; git clone "$REPO" "$APP_DIR"; cd "$APP_DIR"; }
else
    info "جاري تحميل المشروع..."
    git clone "$REPO" "$APP_DIR"
    cd "$APP_DIR"
fi
ok "الكود جاهز في $APP_DIR"

# ── 3. Virtual environment ──
if [ ! -d ".venv" ]; then
    info "إنشاء بيئة افتراضية..."
    python3 -m venv .venv
fi
source .venv/bin/activate
ok "البيئة الافتراضية مفعّلة"

# ── 4. Dependencies ──
info "تثبيت المكتبات..."
pip install --upgrade pip -q 2>/dev/null
pip install -r requirements.txt -q
pip install gunicorn -q
ok "المكتبات مثبّتة"

# ── 5. Environment file ──
if [ ! -f ".env" ]; then
    info "إعداد ملف الإنتاج..."
    SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')

    echo ""
    echo -e "${CYAN}═══ إعداد النظام ═══${NC}"
    read -rp "  اسم المقهى بالعربي [مقهى النموذج]: " CAFE_AR
    CAFE_AR="${CAFE_AR:-مقهى النموذج}"
    read -rp "  اسم المقهى بالإنجليزي [Demo Café]: " CAFE_EN
    CAFE_EN="${CAFE_EN:-Demo Café}"
    read -rp "  البورت [$PORT]: " USER_PORT
    PORT="${USER_PORT:-$PORT}"
    echo ""

    cat > .env <<ENVEOF
DEBUG=False
SECRET_KEY=$SECRET
ALLOWED_HOSTS=localhost,127.0.0.1,*
CAFE_NAME_AR=$CAFE_AR
CAFE_NAME_EN=$CAFE_EN
ENVEOF
    ok "تم إنشاء .env"
else
    ok "ملف .env موجود — لن يتم تغييره"
fi

# ── 6. Database (empty — production) ──
info "إعداد قاعدة البيانات..."
python3 manage.py migrate -v 0
ok "قاعدة البيانات جاهزة (فارغة)"

# ── 7. Admin user + essential data ──
if ! python3 -c "
import django, os
os.environ['DJANGO_SETTINGS_MODULE']='config.settings'
django.setup()
from django.contrib.auth import get_user_model
exit(0 if get_user_model().objects.filter(username='admin').exists() else 1)
" 2>/dev/null; then
    echo ""
    echo -e "${CYAN}═══ إنشاء حساب المدير ═══${NC}"
    while true; do
        read -rsp "  كلمة مرور المدير: " ADMIN_PASS
        echo ""
        read -rsp "  تأكيد كلمة المرور: " ADMIN_PASS2
        echo ""
        if [ "$ADMIN_PASS" = "$ADMIN_PASS2" ] && [ ${#ADMIN_PASS} -ge 4 ]; then
            break
        fi
        echo -e "  ${RED}كلمات المرور غير متطابقة أو أقل من 4 أحرف${NC}"
    done
    python3 manage.py setup_system --admin-pass "$ADMIN_PASS"
    ok "تم إنشاء حساب المدير"
else
    python3 manage.py setup_system --admin-pass "skip" 2>/dev/null || true
    ok "حساب المدير موجود — تم تحديث البيانات الأساسية"
fi

# ── 8. Collect static ──
info "تجميع الملفات الثابتة..."
python3 manage.py collectstatic --noinput -v 0 2>/dev/null || true
ok "الملفات الثابتة جاهزة"

# ── 9. Systemd service (auto-start on boot) ──
SYSTEMD_AVAILABLE=false
if command -v systemctl >/dev/null 2>&1 && [ -d "/etc/systemd/system" ]; then
    SYSTEMD_AVAILABLE=true
fi

if [ "$SYSTEMD_AVAILABLE" = true ] && [ "$(id -u)" = "0" ] || command -v sudo >/dev/null 2>&1; then
    info "إعداد التشغيل التلقائي..."

    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
    VENV_PYTHON="$APP_DIR/.venv/bin/python3"
    GUNICORN_BIN="$APP_DIR/.venv/bin/gunicorn"
    CURRENT_USER=$(whoami)

    SERVICE_CONTENT="[Unit]
Description=Cafe POS System
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$APP_DIR
Environment=PATH=$APP_DIR/.venv/bin:/usr/local/bin:/usr/bin
ExecStart=$GUNICORN_BIN config.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 120
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target"

    if [ "$(id -u)" = "0" ]; then
        echo "$SERVICE_CONTENT" > "$SERVICE_FILE"
        systemctl daemon-reload
        systemctl enable "$SERVICE_NAME" >/dev/null 2>&1
        systemctl restart "$SERVICE_NAME"
        ok "الخدمة مفعّلة — تشتغل تلقائياً عند إعادة التشغيل"
    else
        echo "$SERVICE_CONTENT" | sudo tee "$SERVICE_FILE" >/dev/null 2>&1
        if [ $? -eq 0 ]; then
            sudo systemctl daemon-reload
            sudo systemctl enable "$SERVICE_NAME" >/dev/null 2>&1
            sudo systemctl restart "$SERVICE_NAME"
            ok "الخدمة مفعّلة — تشتغل تلقائياً عند إعادة التشغيل"
        else
            warn "لم يتم إعداد التشغيل التلقائي (بدون صلاحيات sudo)"
            warn "شغّل يدوياً: cd $APP_DIR && source .venv/bin/activate && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT"
        fi
    fi
else
    warn "systemd غير متاح — التشغيل التلقائي غير مدعوم على هذا النظام"
    warn "لتشغيل يدوي: cd $APP_DIR && source .venv/bin/activate && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT"
fi

# ── 10. Get server IP ──
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
if [ -z "$SERVER_IP" ]; then SERVER_IP="localhost"; fi

# ── Done ──
echo ""
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}            ✓ التثبيت اكتمل بنجاح!                  ${NC}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${CYAN}الرابط:${NC}            ${BOLD}http://${SERVER_IP}:${PORT}${NC}"
echo -e "  ${CYAN}اسم المستخدم:${NC}     ${BOLD}admin${NC}"
echo -e "  ${CYAN}كلمة المرور:${NC}      التي أدخلتها أثناء التثبيت"
echo ""
echo -e "  ${CYAN}حالة الخدمة:${NC}      sudo systemctl status $SERVICE_NAME"
echo -e "  ${CYAN}إيقاف:${NC}            sudo systemctl stop $SERVICE_NAME"
echo -e "  ${CYAN}إعادة تشغيل:${NC}     sudo systemctl restart $SERVICE_NAME"
echo -e "  ${CYAN}سجل الأخطاء:${NC}     sudo journalctl -u $SERVICE_NAME -f"
echo ""
echo -e "  ${CYAN}للتحديث لاحقاً:${NC}   cd $APP_DIR && bash update.sh"
echo ""
