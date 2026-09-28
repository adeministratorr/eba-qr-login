#!/usr/bin/env bash
# EBA / ETAP QR giriş tanılama betiği
# Amaç: eta-qr-login paketinin sürüm, servis, WebKit ve sabit endpoint bilgisini toplamak.
# Eylül 2026'daki yeni EBA SSO sistemini (sso.eba.gov.tr: EBA Kod + Karekod +
# OAuth2 e-Devlet/Mebbis/Açıköğretim) ve ölen eski QR JSP'lerini birlikte kapsar.
# Bilinçli olarak kullanıcı cookie/token/parola ve canlı HTTP gövdeleri toplanmaz.

set -u

PKG="eta-qr-login"
# Eski (studentQrcode/teacherQrcode/girisQrcode/getuserinfo) + yeni SSO
# (oauth2, edevlet, mebbis, EBA Kod, karekod/qrTrigger) anahtar kelimeleri.
KW='eba|qr|qrcode|karekod|https?://|wss?://|token|session|redirect|callback|login|auth|sso|oauth|edevlet|mebbis|studentQrcode|teacherQrcode|girisQrcode|qrTrigger|generateDeviceCode|checkDeviceCode|validateUserCode|deviceCode|qrCodeUrl|qr-etap|eba-callback|PRDS|expire_at|lightdm_trigger|qr-trigger|getuserinfo|7777'
STAMP="$(date +%Y%m%d_%H%M%S)"
BASE="${HOME}/eta-qr-lab"
OUT="${BASE}/diagnostic_${STAMP}"

mkdir -p "${OUT}"/{package,service,webkit,endpoints,extract}
echo "Çıktı dizini: ${OUT}"

have() { command -v "$1" >/dev/null 2>&1; }

capture() {
    local file="$1"
    shift
    {
        echo "\$ $*"
        "$@"
    } >"${file}" 2>&1 || true
}

echo "[1/8] Paket bilgisi..."
capture "${OUT}/package/dpkg-query.txt" \
    dpkg-query -W -f='Package: ${Package}\nVersion: ${Version}\nArchitecture: ${Architecture}\nStatus: ${Status}\n' "${PKG}"

capture "${OUT}/package/dpkg-status.txt" dpkg -s "${PKG}"
capture "${OUT}/package/apt-policy.txt" apt-cache policy "${PKG}"
capture "${OUT}/package/apt-show.txt" apt-cache show "${PKG}"
capture "${OUT}/package/files.txt" dpkg -L "${PKG}"

echo "[2/8] Paket dosya tipleri..."
if dpkg -s "${PKG}" >/dev/null 2>&1; then
    while IFS= read -r f; do
        [ -f "$f" ] || continue
        printf '%s\t' "$f"
        file -b "$f" 2>/dev/null || echo "(okunamadı)"
    done < <(dpkg -L "${PKG}") > "${OUT}/package/file-types.txt"
fi

echo "[3/8] Sabit EBA/QR URL ve anahtar kelimeleri..."
: > "${OUT}/package/interesting.txt"
: > "${OUT}/package/urls.txt"

if dpkg -s "${PKG}" >/dev/null 2>&1; then
    while IFS= read -r f; do
        [ -f "$f" ] || continue

        # Metin dosyalarında satır bağlamıyla ara.
        if grep -Iq . "$f" 2>/dev/null; then
            grep -nEi \
                "$KW" \
                "$f" 2>/dev/null | sed "s#^#FILE=${f}:#" >> "${OUT}/package/interesting.txt" || true

            grep -Eo \
                'https?://[^"'"'"'[:space:]<>)]+' \
                "$f" 2>/dev/null | sed "s#^#${f}\t#" >> "${OUT}/package/urls.txt" || true
        else
            # Binary ise yalnızca sabit stringleri tara.
            strings "$f" 2>/dev/null | grep -Ei \
                "$KW" \
                | sed "s#^#FILE=${f}:#" >> "${OUT}/package/interesting.txt" || true
        fi
    done < <(dpkg -L "${PKG}")
fi

sort -u "${OUT}/package/urls.txt" -o "${OUT}/package/urls.txt" 2>/dev/null || true

echo "[4/8] systemd / LightDM bağlantısı..."
for svc in ebaqr.service eta-qr-login.service; do
    systemctl show "$svc" \
        -p Id -p LoadState -p ActiveState -p SubState -p FragmentPath -p ExecStart \
        --no-pager > "${OUT}/service/${svc}.show.txt" 2>&1 || true
    systemctl cat "$svc" --no-pager \
        > "${OUT}/service/${svc}.unit.txt" 2>&1 || true
done

grep -RInE \
    'eta-qr-login|ebaqr|studentQrcode|getuserinfo|localhost[ :]+7777' \
    /etc/lightdm /usr/share/lightdm /etc/systemd /usr/lib/systemd /lib/systemd \
    2>/dev/null > "${OUT}/service/integration-grep.txt" || true

echo "[5/8] WebKit/GTK sürümleri..."
{
    echo "=== dpkg-query ==="
    dpkg-query -W -f='${Package}\t${Version}\n' 2>/dev/null \
        | grep -Ei 'webkit|gir1\.2-webkit|gtk|python3-gi' || true

    echo
    echo "=== ldconfig ==="
    ldconfig -p 2>/dev/null | grep -Ei 'webkit|javascriptcore' || true
} > "${OUT}/webkit/versions.txt"

echo "[6/8] Eski ve yeni EBA giriş endpoint durumları..."
if have curl; then
    check_url() {
        local label="$1"
        local url="$2"
        {
            echo "LABEL=${label}"
            echo "URL=${url}"
            curl -L -sS \
                -o /dev/null \
                --max-time 15 \
                -w 'HTTP=%{http_code}\nFINAL_URL=%{url_effective}\nCONTENT_TYPE=%{content_type}\nREDIRECTS=%{num_redirects}\n' \
                "$url" || true
        } > "${OUT}/endpoints/${label}.txt"
    }

    # Ölen eski QR JSP'leri (Eylül 2026 itibarıyla üçü de HTTP 404 veriyor).
    check_url "old_student_qrcode" \
        "https://giris.eba.gov.tr/EBA_GIRIS/studentQrcode.jsp"

    check_url "old_teacher_qrcode" \
        "https://giris.eba.gov.tr/EBA_GIRIS/teacherQrcode.jsp"

    check_url "old_giris_qrcode" \
        "https://giris.eba.gov.tr/EBA_GIRIS/girisQrcode.jsp"

    # Yeni SSO giriş sayfası (EBA Kod + Karekod + OAuth2 seçenekleri).
    check_url "new_sso_login" \
        "https://sso.eba.gov.tr/login"

    # Yeni akışın ders girişi (giris.jsp'ye yönlenir).
    check_url "new_ders" \
        "https://ders.eba.gov.tr/"

    # Yeni SSO QR / EBA-Kod API uçları (resmi login.js'ten çıkarılan akış):
    #   generateDeviceCode -> {deviceCode, qrCodeUrl} (60 sn geçerli QR)
    #   checkDeviceCode (2.5 sn'de bir poll) -> {url}
    #   validateUserCode (POST: code+non+client_id) -> {url}
    # Parametresiz çağrı hata sayfası döner (400/200); burada yalnızca
    # ucun yaşadığı (404 olmadığı) doğrulanır, gövde saklanmaz.
    check_url "new_api_generate_device_code" \
        "https://sso.eba.gov.tr/generateDeviceCode"

    check_url "new_api_check_device_code" \
        "https://sso.eba.gov.tr/checkDeviceCode"

    check_url "new_api_validate_user_code" \
        "https://sso.eba.gov.tr/validateUserCode"

    # qr-etap röle sunucusu (FastAPI/uvicorn): tahta wss ile bağlanır,
    # telefon check/callback uçlarını kullanır.
    check_url "relay_root" \
        "https://qr-etap.eba.gov.tr/"

    # Sahte uuid ile check: 200 + available:false dönerse API yaşıyor demektir.
    check_url "relay_check_bogus" \
        "https://qr-etap.eba.gov.tr/api/v1/check/PRDS-00000000-0000-0000-0000-000000000000"

    # Röle API haritasında beklenen uçlar mevcut mu? (gövde saklanmaz.)
    {
        echo "URL=https://qr-etap.eba.gov.tr/openapi.json"
        curl -L -sS --max-time 15 \
            "https://qr-etap.eba.gov.tr/openapi.json" 2>/dev/null \
            | grep -Eo 'check_uuid|verify_token|eba-callback|/api/v1/check|TokenRequest' \
            | sort | uniq -c || true
    } > "${OUT}/endpoints/relay_api_markers.txt"

    # wss el sıkışması: 101 = röle tahta bağlantılarını kabul ediyor.
    {
        echo "URL=wss://qr-etap.eba.gov.tr/api/v1/ws/<MAC>"
        out=$(curl -sS -i --max-time 8 \
            -H "Connection: Upgrade" -H "Upgrade: websocket" \
            -H "Sec-WebSocket-Version: 13" \
            -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
            "https://qr-etap.eba.gov.tr/api/v1/ws/00:00:00:00:00:00" 2>&1 | head -n1)
        echo "RESPONSE=${out:-<baglanti kurulamadi>}"
        echo "Not: '101 Switching Protocols' rölenin ayakta olduğunu gösterir."
    } > "${OUT}/endpoints/relay_ws_handshake.txt"

    # SSO sayfasında QR / EBA Kod / OAuth belirteçleri mevcut mu?
    # Sayfa gövdesi saklanmaz; yalnızca eşleşen belirteçler sayılır.
    {
        echo "URL=https://sso.eba.gov.tr/login"
        curl -L -sS --max-time 15 \
            "https://sso.eba.gov.tr/login" 2>/dev/null \
            | grep -Eo -i \
                'qrTrigger|qr\.svg|EBA Kod|Karekod|oauth2/authorization/[a-z]+|/(mebbis|aol)/authorize' \
            | sort | uniq -c || true
    } > "${OUT}/endpoints/new_sso_markers.txt"

    # Canlı kimlik endpoint gövdesi özellikle indirilmez.
    {
        echo "Not: getuserinfo endpointine oturum cookie/token gönderen canlı istek yapılmadı."
        echo "Eski kaynakta görülen adres:"
        echo "https://uygulama-ebaders.eba.gov.tr/ders/FrontEndService//home/user/getuserinfo"
    } > "${OUT}/endpoints/old_getuserinfo.txt"
else
    echo "curl bulunamadı." > "${OUT}/endpoints/curl-not-found.txt"
fi

echo "[7/8] Mevcut .deb paketini indirmeyi dene..."
if have apt; then
    (
        cd "${OUT}/extract" || exit 0
        apt download "${PKG}" > apt-download.txt 2>&1 || true

        deb="$(find . -maxdepth 1 -type f -name "${PKG}_*.deb" | head -n1)"
        if [ -n "${deb:-}" ] && have dpkg-deb; then
            mkdir -p unpacked control
            dpkg-deb -x "$deb" unpacked/ > dpkg-deb-x.txt 2>&1 || true
            dpkg-deb -e "$deb" control/ > dpkg-deb-e.txt 2>&1 || true

            grep -RInE \
                "$KW" \
                unpacked/ 2>/dev/null > unpacked-interesting.txt || true
        fi
    )
fi

echo "[8/8] Özet üret..."
{
    echo "EBA / ETAP QR Tanılama Özeti"
    echo "Tarih: $(date -Is)"
    echo
    echo "=== Paket ==="
    cat "${OUT}/package/dpkg-query.txt" 2>/dev/null || true
    echo
    echo "=== Servis ==="
    cat "${OUT}/service/ebaqr.service.show.txt" 2>/dev/null || true
    echo
    echo "=== WebKit ==="
    cat "${OUT}/webkit/versions.txt" 2>/dev/null || true
    echo
    echo "=== Endpointler ==="
    for f in "${OUT}"/endpoints/*.txt; do
        [ -f "$f" ] || continue
        echo "--- $(basename "$f") ---"
        cat "$f"
    done
    echo
    echo "=== Bulunan sabit URL'ler ==="
    cat "${OUT}/package/urls.txt" 2>/dev/null || true
} > "${OUT}/SUMMARY.txt"

ARCHIVE="${BASE}/eta-qr-diagnostic_${STAMP}.tar.gz"
tar -C "${BASE}" -czf "${ARCHIVE}" "$(basename "${OUT}")"

echo
echo "Tamamlandı."
echo "Özet:   ${OUT}/SUMMARY.txt"
echo "Arşiv:  ${ARCHIVE}"
echo
echo "Arşiv canlı cookie/token/parola toplamayı hedeflemez."
echo "Yine de paylaşmadan önce şu kontrolü çalıştırabilirsiniz:"
echo "grep -RniE 'authorization|bearer|cookie|password|passwd|[0-9]{11}' '${OUT}'"
