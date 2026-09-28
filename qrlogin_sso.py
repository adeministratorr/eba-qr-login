#!/usr/bin/env python3
"""eta-qr-login greeter uyarlaması: eski PRDS/röle akışı yerine EBA SSO akışı.

Eski mimari (eta-qr-login 0.2.6, README'deki analize göre):
  ebaqr.service --wss--> qr-etap rölesi --unix soket--> greeter (qrlogin.py +
  QrWidget PRDS-... çizer, expire_at geri sayımı) ; validation gelince
  eta-usb-login kullanıcıyı bulur/oluşturur, /var/lib/lightdm/pardus-greeter
  soketi üzerinden lightdm_trigger(uname, passwd) ile oturum açılır.

Bu dosya aynı dış arayüzü koruyup yalnızca "QR sağlayıcıyı" değiştirir:
  röle+PRDS  -->  SSO device-akışı (mint -> generateDeviceCode -> sso: QR,
                   2.5 sn poll -> {code} -> userinfo -> yerel kullanıcı)

Gerekenler (tahta imajına işlenecek):
  - TAHTA_AUTH_BASE: code->token->userinfo takasını yapan istemci backend
    adresi (PKCE verifier backend'dedir; tahta kodu buraya POST'lar).
    Geçici olarak devdep adresi bırakıldı, ETAP imajında değiştirilmeli.
    DİKKAT: devdep mint'in prod SSO'da geçerli olduğu doğrulanmadı;
    baglan() bu yüzden best-effort çalışır, mint olmazsa mintsiz devam
    eder (generateDeviceCode boş parametreyle de QR üretir).
  - Ağ erişimi: sso.eba.gov.tr (443) + TAHTA_AUTH_BASE.

Canlı parola bu dosyada tutulmaz; yerel parola eskisi gibi
bcrypt(sha1(eba_id)) ile türetilir (eta-usb-login).
"""
import base64
import http.cookiejar
import json
import os
import sys
import time
import urllib.parse
import urllib.request

SSO = "https://sso.eba.gov.tr"
MINT_BASE = os.environ.get(
    "TAHTA_AUTH_BASE", "https://devdep.eba.gov.tr/api")
MINT_PATH = "/v1/authz/login"
MINT_REDIRECT = os.environ.get(
    "TAHTA_AUTH_REDIRECT", "https://devdep.eba.gov.tr/asistan/sso/giris")

# Eski paketle aynı soket yolları (arayüz korunur)
QR_TRIGGER_SOCK = "/run/etap/qr-trigger"
GREETER_SOCK = "/var/lib/lightdm/pardus-greeter"
EBAQR_SOCK = "/var/lib/lightdm/ebaqr"

POLL_INTERVAL = 2.5
QR_TTL = 55  # sunucu 60 sn; erken yenile (login.js ile aynı)

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class SsoQrProvider:
    """Tek oturumda: mint -> login sayfası -> QR üret -> yokla."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.client_id = ""
        self.nonce = ""
        self.login_url = ""

    def _req(self, url, params=None, referer=None):
        if params:
            url += "?" + urllib.parse.urlencode(params)
        h = {"User-Agent": UA,
             "Accept": "application/json, text/plain, */*",
             "Accept-Language": "tr-TR,tr;q=0.9",
             "X-Requested-With": "XMLHttpRequest",
             "Sec-Fetch-Site": "same-origin",
             "Sec-Fetch-Mode": "cors"}
        if referer:
            h["Referer"] = referer
            h["Origin"] = SSO
        return urllib.request.Request(url, headers=h)

    def baglan(self):
        """Backend'den taze authorize URL al, login sayfasına yürü, id'leri oku.

        Best-effort: mint/parse başarısız olursa boş id'lerle mintsiz devam
        edilir (generateDeviceCode boş parametreyle de QR üretir, doğrulandı).
        """
        import re
        try:
            qs = urllib.parse.urlencode(
                {"redirect_uri": MINT_REDIRECT, "platform": "web"})
            req = urllib.request.Request(
                MINT_BASE + MINT_PATH + "?" + qs,
                headers={"User-Agent": UA, "Accept": "application/json"})
            mint = json.load(self.op.open(req, timeout=15))
            self.login_url = mint["data"]["redirect_url"]
            html = self.op.open(self.login_url, timeout=15).read().decode(
                "utf-8", "replace")
            mn = re.search(r'loginNonce" value="([^"]*)"', html)
            mc = re.search(r'loginClientId" value="([^"]*)"', html)
            self.nonce = mn.group(1) if mn else ""
            self.client_id = mc.group(1) if mc else ""
        except Exception as e:
            print(f"mint/parse başarısız, mintsiz devam: {e}")
            self.login_url = ""
            self.nonce = ""
            self.client_id = ""
        return True

    def qr_uret(self):
        """(deviceCode, qr_png_bytes) döndürür.

        PNG sunucunun ürettiği resmi QR'dır; içerik biçimi (sso:...) sunucu
        garantisindedir, istemci ayrıca doğrulamaz.
        """
        st, data = self._get_json(
            "/generateDeviceCode",
            {"client_id": self.client_id, "state": self.nonce})
        if st != 200 or not data or "qrCodeUrl" not in data:
            raise RuntimeError(f"QR üretilemedi: HTTP {st}")
        png = base64.b64decode(data["qrCodeUrl"].split(",", 1)[1])
        return data["deviceCode"], png

    def _get_json(self, path, params):
        req = self._req(SSO + path, params, referer=self.login_url or SSO + "/login")
        try:
            with self.op.open(req, timeout=15) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, None
        except Exception:
            # Bağlantı/timeout: çağıran yeniden dener (poll) ya da hata verir.
            return None, None

    def onay_bekle(self, device_code, toplam_sn):
        """{code,state} içeren redirect bilgisini döndürür ya da None.

        403 hız-limitidir (geçici, IP bazlı): çıkış yerine bekleyip aynı
        kodla devam edilir (login.js davranışı).
        """
        bitis = time.time() + toplam_sn
        bekleme = POLL_INTERVAL
        while time.time() < bitis:
            st, data = self._get_json("/checkDeviceCode", {
                "deviceCode": device_code,
                "clientId": self.client_id,
                "non": self.nonce})
            if st == 403:
                time.sleep(bekleme)
                bekleme = min(bekleme * 2, 20)
                continue
            bekleme = POLL_INTERVAL
            if st == 200 and data and data.get("url"):
                return data["url"]
            time.sleep(POLL_INTERVAL)
        return None


def exchange_code_for_user(code, state, auth_base=None, redirect_uri=None):
    """Exchanges authorization code and state for access token and user identity.

    1. Calls backend /v1/authz/login (POST) to perform token exchange with PKCE verifier.
    2. Decodes JWT payload to extract user claims (sub, name, role).
    3. Queries userinfo_endpoint (https://sso.eba.gov.tr/oauth2/userinfo) with Bearer token.
    4. Returns a dictionary with uid, uname, utype, full_name, and raw tokens.
    """
    base = auth_base or MINT_BASE
    target_redirect = redirect_uri or MINT_REDIRECT
    exchange_url = base + MINT_PATH

    payload = {
        "code": code,
        "platform": "web",
        "state": state,
        "redirect_uri": target_redirect
    }

    req = urllib.request.Request(
        exchange_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": UA,
            "Origin": "https://devdep.eba.gov.tr",
            "Referer": target_redirect
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
    except Exception as e:
        raise RuntimeError(f"Token exchange failed at {exchange_url}: {e}")

    if not data or data.get("status") != "success":
        raise RuntimeError(f"Unexpected token exchange response: {data}")

    auth_data = data.get("data", {})
    access_token = auth_data.get("access_token")
    refresh_token = auth_data.get("refresh_token")

    if not access_token:
        raise RuntimeError("Token exchange succeeded but access_token is missing")

    # 1. Decode JWT payload
    jwt_claims = {}
    try:
        token_parts = access_token.split(".")
        if len(token_parts) >= 2:
            padded = token_parts[1] + "=" * ((4 - len(token_parts[1]) % 4) % 4)
            jwt_claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception as je:
        print(f"Warning: Failed to decode JWT payload: {je}", file=sys.stderr)

    # 2. Query userinfo endpoint with Bearer token
    userinfo = {}
    try:
        ui_req = urllib.request.Request(
            SSO + "/oauth2/userinfo",
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": UA,
                "Accept": "application/json"
            }
        )
        with urllib.request.urlopen(ui_req, timeout=10) as ui_resp:
            userinfo = json.load(ui_resp)
    except Exception as ue:
        print(f"Warning: /oauth2/userinfo query returned error: {ue}", file=sys.stderr)

    uid = str(userinfo.get("sub") or jwt_claims.get("sub") or jwt_claims.get("tckn") or "")
    uname = str(userinfo.get("preferred_username") or userinfo.get("username") or
                jwt_claims.get("preferred_username") or jwt_claims.get("username") or uid)

    # Extract human-readable full name from profiles
    full_name = str(userinfo.get("name") or jwt_claims.get("name") or "")
    profiles = jwt_claims.get("profiles", [])
    if isinstance(profiles, list) and len(profiles) > 0:
        p0 = profiles[0]
        fn = p0.get("first_name", "")
        ln = p0.get("last_name", "")
        if fn or ln:
            full_name = f"{fn} {ln}".strip()
    if not full_name:
        full_name = uname

    utype = str(userinfo.get("role") or jwt_claims.get("role") or "teacher")

    return {
        "uid": uid,
        "uname": uname,
        "full_name": full_name,
        "utype": utype,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "userinfo": userinfo,
        "jwt_claims": jwt_claims
    }


def koda_karsilik_kullanici(code, state):
    """Resolve code and state to (uid, uname) using backend token exchange."""
    user = exchange_code_for_user(code, state)
    return user["uid"], user["uname"]


def main():
    import argparse
    ap = argparse.ArgumentParser(description="SSO QR greeter döngüsü")
    ap.add_argument("--toplam-sn", type=int, default=180)
    ap.add_argument("--qr-out", default="/tmp/greeter-qr.png")
    ap.add_argument("--terminal-qr", action="store_true", default=True,
                    help="Render ASCII QR code in terminal")
    ap.add_argument("--no-terminal-qr", dest="terminal_qr", action="store_false",
                    help="Disable ASCII QR rendering in terminal")
    args = ap.parse_args()

    saglayici = SsoQrProvider()
    saglayici.baglan()
    print(f"oturum hazır: client_id={saglayici.client_id[:8]}...", flush=True)

    bitis = time.time() + args.toplam_sn
    while time.time() < bitis:
        kod, png = saglayici.qr_uret()
        with open(args.qr_out, "wb") as f:
            f.write(png)
        # QrWidget draws png on screen and countdowns QR_TTL
        print(f"QR ekranda: {args.qr_out} ({kod[:16]}...)", flush=True)

        if args.terminal_qr:
            try:
                import qrcode
                qr_term = qrcode.QRCode()
                qr_term.add_data(kod)
                print("\n=== SCAN WITH EBA MOBILE APP ===")
                qr_term.print_ascii(invert=True)
                print("================================\n")
            except Exception as e:
                print(f"Terminal QR failed: {e}")

        url = saglayici.onay_bekle(kod, min(QR_TTL, bitis - time.time()))
        if url:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            code = q.get("code", [None])[0]
            state = q.get("state", [None])[0]
            print(f"\n[STEP 1 SUCCESS] Approval URL received: code={'YES' if code else 'NO'}, state={'YES' if state else 'NO'}", flush=True)
            print("onay geldi, kullanıcı eşleştiriliyor...", flush=True)
            uid, uname = koda_karsilik_kullanici(code, state)
            # eta-usb-login: create or find user (bcrypt(sha1(eba_id)))
            # lightdm_trigger(uname, passwd) -> GREETER_SOCK
            print(f"giriş: {uname} ({uid})", flush=True)
            return 0
    print("süre doldu.")
    return 3


if __name__ == "__main__":
    sys.exit(main())
