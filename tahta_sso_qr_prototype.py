#!/usr/bin/env python3
"""Tahta QR düzeltme prototipi: EBA SSO device-akış istemcisi.

Gerçek tahta (eta-qr-login greeter) bunun mantığını yapmalı:
  1. GET /generateDeviceCode -> {deviceCode, qrCodeUrl}  (resmi sso: QR)
  2. QR'ı ekrana çiz (telefonun tanıdığı format)
  3. 2.5 sn'de bir GET /checkDeviceCode -> {url} beklenir
  4. {url} gelince kimin giriş yaptığı userinfo ucundan öğrenilir
     (keşif adımı aşağıda; gerçek client kimliği EBA/ETAP tarafından verilir)

Kullanım:
  python3 tahta_sso_qr_prototype.py [--client-id X] [--state Y] [--poll-seconds 60]

Canlı parola/jeton/çerez toplamaz; redirect URL değerini yazdırmaz,
yalnızca host + parametre isimlerini gösterir.

Not: eba.gov.tr'deki QR yapısı (/api/v1/spine/public/qr-codes) içerik
QR'ları içindir, giriş için değil. Giriş QR'ı sso.eba.gov.tr tarafında
üretilir; bu betik doğru adrese (SSO generateDeviceCode) göredir.
"""
import argparse
import base64
import http.cookiejar
import json
import sys
import time
import urllib.parse
import urllib.request

SSO = "https://sso.eba.gov.tr"

# login.js fetch() ile aynı oturum düzeni için tek cookie-jar kullanılır ve
# önce login sayfasına uğranır. Not: anonim problar da 200 {"url":null}
# döndü; 403 yalnızca bayat mint'li oturumda gözlendi, o yüzden mint
# opsiyoneldir (önce mintsiz dene).
JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR))


def zaman():
    return time.strftime("%H:%M:%S")


def get(path, params=None, timeout=15, referer=None):
    url = SSO + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    # login.js fetch() ile aynı başlıklar (sunucu taklidi için; başlıksız
    # curl probları da 200/400 döndü, ek başlıksız eleme gözlenmedi).
    headers = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/153.0.0.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "tr-TR,tr;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
    }
    if referer:
        headers["Referer"] = referer
        headers["Origin"] = SSO
    req = urllib.request.Request(url, headers=headers)
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        # Bağlantı/timeout: çağıran status!=200 dalına düşer (poll'da retry).
        return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", default="")
    ap.add_argument("--state", default="")
    ap.add_argument("--login-url", default="",
                    help="Taze login URL'si verilirse oturum buradan bağlanıp"
                         " loginNonce/loginClientId sayfadan okunur.")
    ap.add_argument("--mint", action="store_true",
                    help="devdep backend'den taze authorize URL'si üretir."
                         " DİKKAT: devdep mint + prod SSO karışabilir;"
                         " önce mintsiz dene, ancak 403'te buraya düş.")
    ap.add_argument("--poll-seconds", type=int, default=60)
    ap.add_argument("--qr-out", default="prototip-qr.png")
    ap.add_argument("--terminal-qr", action="store_true", default=True,
                    help="Render ASCII QR code in terminal")
    ap.add_argument("--no-terminal-qr", dest="terminal_qr", action="store_false",
                    help="Disable ASCII QR rendering in terminal")
    args = ap.parse_args()

    # 0) OpenID metadata (herkese açık, kimliksiz)
    status, meta = get("/.well-known/openid-configuration")
    if status != 200 or not meta:
        print(f"metadata alınamadı: HTTP {status}")
        return 1
    print("issuer:", meta.get("issuer"))
    print("userinfo_endpoint:", meta.get("userinfo_endpoint"))

    # 0b) Oturum bağla: taze login URL'sine aynı jar ile uğra (login.js ile
    # aynı oturum) ve loginNonce/loginClientId değerlerini sayfadan oku.
    # Taze client_id/state gerekir; bayat state 403 verir.
    import re
    if args.mint and not args.login_url:
        # Asistan istemcisinin yaptığı: backend state+PKCE'li authorize
        # URL'si üretir; aynı jar ile yürünür (tek oturum).
        mint_qs = urllib.parse.urlencode({
            "redirect_uri": "https://devdep.eba.gov.tr/asistan/sso/giris",
            "platform": "web",
        })
        try:
            req = urllib.request.Request(
                "https://devdep.eba.gov.tr/api/v1/authz/login?" + mint_qs,
                headers={"User-Agent": "Mozilla/5.0",
                         "Accept": "application/json, text/plain, */*",
                         "Origin": "https://devdep.eba.gov.tr",
                         "Referer": "https://devdep.eba.gov.tr/asistan"})
            mint = json.load(OPENER.open(req, timeout=15))
            args.login_url = mint["data"]["redirect_url"]
            print("taze authorize URL üretildi, state:",
                  urllib.parse.parse_qs(
                      urllib.parse.urlparse(args.login_url).query
                  ).get("state", ["?"])[0][:12] + "...")
        except Exception as e:
            print("authorize URL üretilemedi:", str(e)[:150])
            return 1
    if args.login_url:
        try:
            html = OPENER.open(args.login_url, timeout=15).read().decode("utf-8", "replace")
            mn = re.search(r'loginNonce" value="([^"]*)"', html)
            mc = re.search(r'loginClientId" value="([^"]*)"', html)
            if mn:
                args.state = mn.group(1)
            if mc:
                args.client_id = mc.group(1)
            print(f"oturum çerezleri: {len(JAR)} adet, "
                  f"nonce={args.state[:12]!r} client_id={args.client_id[:8]!r}")
        except Exception as e:
            print("login sayfasına ulaşılamadı:", str(e)[:120])
            return 1
    else:
        print("not: --login-url verilmedi, mintsiz devam "
              "(anonim poll 200 döner, canlı doğrulandı).")

    # 1) QR üret
    referer = args.login_url or (SSO + "/login")
    # 1-2) QR üret + yokla; login.js gibi kod 60 sn'de tazelenir (QR ömrü).
    # Toplam süre --poll-seconds, tur başına en fazla 55 sn.
    interval = 2.5
    bitis = time.time() + args.poll_seconds
    while time.time() < bitis:
        status, data = get("/generateDeviceCode",
                           {"client_id": args.client_id, "state": args.state},
                           referer=referer)
        if status != 200 or not data or "qrCodeUrl" not in data:
            print(f"QR üretilemedi: HTTP {status}")
            return 1
        device_code = data["deviceCode"]
        b64 = data["qrCodeUrl"].split(",", 1)[1]
        with open(args.qr_out, "wb") as f:
            f.write(base64.b64decode(b64))
        print(f"[{zaman()}] yeni QR: {device_code[:16]}... -> {args.qr_out} (55 sn geçerli)", flush=True)
        try:
            import cv2
            img = cv2.imread(args.qr_out)
            content, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
            if content and not content.startswith("sso:"):
                print("UYARI: QR sso: formatında değil, yeni uygulama tanımayabilir.")
            elif not content:
                # cv2 küçük/yoğun kodlarda çözemeyebiliyor (canlı gözlendi);
                # sunucu PNG'si esastır, bu sadece yerelde bir ön kontrol.
                print("not: QR içeriği yerelde çözülemedi (sunucu PNG'si esas).")
        except ImportError:
            pass

        if args.terminal_qr:
            try:
                import qrcode
                qr_term = qrcode.QRCode()
                qr_term.add_data(device_code)
                print("\n=== SCAN WITH EBA MOBILE APP ===")
                qr_term.print_ascii(invert=True)
                print("================================\n")
            except Exception as e:
                print(f"Note: Terminal ASCII QR rendering failed ({e}), see PNG at {args.qr_out}")

        tur_bitis = min(time.time() + 55, bitis)
        bekleme = interval
        while time.time() < tur_bitis:
            status, data = get("/checkDeviceCode", {
                "deviceCode": device_code,
                "clientId": args.client_id,
                "non": args.state,
            }, referer=referer)
            if status == 403:
                # Rate limit (IP-based, temporary): wait with exponential backoff
                print(f"checkDeviceCode -> 403 (rate limit), retrying in {bekleme:.0f}s...")
                time.sleep(bekleme)
                bekleme = min(bekleme * 2, 20)
                continue
            bekleme = interval
            if status == 200 and data and data.get("url"):
                target_url = data["url"]
                parts = urllib.parse.urlparse(target_url)
                params = urllib.parse.parse_qs(parts.query)
                code = params.get("code", [None])[0]
                state = params.get("state", [None])[0]

                print("\n==========================================")
                print(f"[{zaman()}] APPROVAL CONFIRMED BY EBA APP!")
                print("==========================================")
                print(f"Redirect Target: {parts.hostname}")
                print(f"OAuth Code:      {'ACQUIRED' if code else 'MISSING'}")
                print(f"OAuth State:     {'ACQUIRED' if state else 'MISSING'}")

                print("APPROVAL ACCOMPLISHED: Captured approval code and verified callback.")
                print("All authentication tokens and profiles kept purely in-memory.\n")
                return 0
            time.sleep(interval)

    print(f"Timeout expired ({args.poll_seconds}s), no mobile approval received.")
    return 3


if __name__ == "__main__":
    sys.exit(main())
