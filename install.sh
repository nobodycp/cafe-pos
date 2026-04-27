#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  نظام نقاط البيع — تثبيت إنتاجي بأمر واحد
#
#  git clone https://github.com/nobodycp/cafe-pos.git
#  cd cafe-pos && bash install.sh
# ══════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${CYAN}[*]${NC} $1"; }
ok()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
fail()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

PORT="${PORT:-8000}"
SERVICE_NAME="cafe-pos"

echo ""
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}     نظام نقاط البيع — تثبيت تلقائي كامل       ${NC}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════${NC}"
echo ""

# ── Must run from project root ──
if [ ! -f "manage.py" ]; then
    fail "شغّل هذا السكريبت من داخل مجلد المشروع:
    git clone https://github.com/nobodycp/cafe-pos.git
    cd cafe-pos && bash install.sh"
fi

APP_DIR="$(pwd)"

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

# ── 2. Auto-install python3-venv if missing ──
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
    info "تثبيت python3-venv..."
    if command -v apt-get >/dev/null 2>&1; then
        if [ "$(id -u)" = "0" ]; then
            apt-get update -qq && apt-get install -y -qq "python${PY_VER}-venv" python3-pip >/dev/null 2>&1
        else
            sudo apt-get update -qq && sudo apt-get install -y -qq "python${PY_VER}-venv" python3-pip >/dev/null 2>&1
        fi
        ok "تم تثبيت python3-venv"
    else
        fail "python3-venv غير مثبّت. ثبّته يدوياً: sudo apt install python${PY_VER}-venv"
    fi
fi

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
    # عنوان يظهر في المتصفح (IP العام أو الدومين) — مهم لـ ALLOWED_HOSTS و CSRF
    DETECTED_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
    DETECTED_IP="${DETECTED_IP:-}"
    echo ""
    read -rp "  عنوان الوصول من المتصفح (IP العام أو الدومين) [$DETECTED_IP]: " ACCESS_HOST
    ACCESS_HOST="${ACCESS_HOST:-$DETECTED_IP}"
    if [ -z "$ACCESS_HOST" ]; then
        AH_LIST="localhost,127.0.0.1,*"
        CSRF_LINE=""
        info "لم يُكتشف عنوان — ALLOWED_HOSTS=* (إذا فشل تسجيل الدخول أضف في .env: CSRF_TRUSTED_ORIGINS=http://IP_العام:$PORT)"
    else
        AH_LIST="localhost,127.0.0.1,${ACCESS_HOST},*"
        CSRF_LINE="CSRF_TRUSTED_ORIGINS=http://${ACCESS_HOST}:${PORT}"
    fi
    echo ""

    cat > .env <<ENVEOF
DEBUG=False
SECRET_KEY=$SECRET
ALLOWED_HOSTS=$AH_LIST
CAFE_NAME_AR=$CAFE_AR
CAFE_NAME_EN=$CAFE_EN
${CSRF_LINE}
ENVEOF
    ok "تم إنشاء .env"
else
    ok "ملف .env موجود — لن يتم تغييره"
fi

# ── 6. Database ──
info "إعداد قاعدة البيانات..."
python3 manage.py migrate -v 0
ok "قاعدة البيانات جاهزة"

# ── 7. Admin user + essential data ──
ADMIN_EXISTS=$(python3 -c "
import django, os
os.environ['DJANGO_SETTINGS_MODULE']='config.settings'
django.setup()
from django.contrib.auth import get_user_model
print('yes' if get_user_model().objects.filter(username='admin').exists() else 'no')
" 2>/dev/null)

if [ "$ADMIN_EXISTS" = "no" ]; then
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
    ok "حساب المدير موجود"
fi

# ── 8. Collect static ──
info "تجميع الملفات الثابتة..."
python3 manage.py collectstatic --noinput -v 0 2>/dev/null || true
ok "الملفات الثابتة جاهزة"

# ── 9. Systemd service ──
if command -v systemctl >/dev/null 2>&1 && [ -d "/etc/systemd/system" ]; then
    info "إعداد التشغيل التلقائي..."

    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
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

    WRITE_OK=false
    if [ "$(id -u)" = "0" ]; then
        echo "$SERVICE_CONTENT" > "$SERVICE_FILE"
        WRITE_OK=true
    elif command -v sudo >/dev/null 2>&1; then
        echo "$SERVICE_CONTENT" | sudo tee "$SERVICE_FILE" >/dev/null 2>&1 && WRITE_OK=true
    fi

    if [ "$WRITE_OK" = true ]; then
        if [ "$(id -u)" = "0" ]; then
            systemctl daemon-reload
            systemctl enable "$SERVICE_NAME" >/dev/null 2>&1
            systemctl restart "$SERVICE_NAME"
        else
            sudo systemctl daemon-reload
            sudo systemctl enable "$SERVICE_NAME" >/dev/null 2>&1
            sudo systemctl restart "$SERVICE_NAME"
        fi
        ok "الخدمة مفعّلة — تشتغل تلقائياً عند إعادة التشغيل"
    else
        warn "لم يتم إعداد التشغيل التلقائي (بدون صلاحيات)"
    fi
else
    warn "systemd غير متاح"
fi

# ── 9b. Firewall (UFW) — لا نغلق SSH (22) أبداً ──
_ufw() {
    if [ "$(id -u)" = "0" ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        return 1
    fi
}
if command -v ufw >/dev/null 2>&1; then
    if ufw status 2>/dev/null | grep -qi "Status: active"; then
        info "جدار UFW مفعّل — التأكد من السماح بـ SSH ثم المنفذ $PORT..."
        # أهم خط: SSH على 22 — بدونها قد تُقفل الجلسة إذا فُعّل UFW لاحقاً بدون قاعدة SSH
        _ufw ufw allow OpenSSH >/dev/null 2>&1 || _ufw ufw allow 22/tcp comment "ssh" >/dev/null 2>&1 || true
        _ufw ufw allow "$PORT"/tcp comment "cafe-pos" >/dev/null 2>&1 || true
        ok "UFW: السماح بـ SSH (OpenSSH/22) و TCP $PORT"
    fi
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
echo -e "${YELLOW}═══ إذا المتصفح يعطي «timeout» أو لا يفتح الرابط ═══${NC}"
echo -e "  1) في لوحة مزوّد السحابة (Hetzner / DigitalOcean / AWS …):"
echo -e "     أضف قاعدة ${BOLD}Inbound${NC} — TCP — المنفذ ${BOLD}$PORT${NC} — المصدر 0.0.0.0/0"
echo -e "  2) على السيرفر إذا UFW مفعّل (لا تنسَ SSH على 22 أولاً):"
echo -e "     ${BOLD}sudo ufw allow OpenSSH${NC}   أو   ${BOLD}sudo ufw allow 22/tcp${NC}"
echo -e "     ${BOLD}sudo ufw allow $PORT/tcp && sudo ufw reload${NC}"
echo -e "     لا تشغّل ${BOLD}sudo ufw enable${NC} قبل التأكد أن قواعد SSH و$PORT موجودة."
echo -e "  3) تحقق من السيرفر نفسه:"
echo -e "     ${BOLD}curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/${NC}  ← يجب أن يظهر 200 أو 301 أو 302"
echo ""
