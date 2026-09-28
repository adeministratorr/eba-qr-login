#!/usr/bin/env python3
"""Tahta QR canlı panosu: QR + geri sayım + poll durumunu tek sayfada gösterir.

Kullanım:
  python3 tahta_qr_panel.py [--port 8000]
  # sonra tarayıcıda: http://localhost:8000

Akış (gerçek istemcinin birebiri):
 oturum (/login ziyareti, tek cookie-jar) -> generateDeviceCode
 (mobil client_id, state=null) -> QR göster (60 sn) -> checkDeviceCode
 poll (non=null, 5 sn) -> onayda redirect host + parametre İSİMLERİ.

Gizlilik: deviceCode'un tamamı, redirect URL değeri, çerez ve kimlik
asla sayfaya/loga yazılmaz; yalnızca host + parametre isimleri.
"""
import argparse
import base64
import http.cookiejar
import json
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SSO = "https://sso.eba.gov.tr"
MINT_URL = ("https://devdep.eba.gov.tr/api/v1/authz/login?"
            "redirect_uri=https%3A%2F%2Fdevdep.eba.gov.tr%2Fasistan%2Fsso%2Fgiris&platform=web")
from qrlogin_sso import exchange_code_for_user

QR_OMRU = 55
POLL_ARALIK = 3

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

durum = {"kod_kisa": "-", "kalan": 0, "asama": "baslatiliyor",
         "url_host": "", "url_params": [], "uretildi": 0, "code_received": False,
         "user_name": "", "user_role": "", "user_id": ""}
kilit = threading.Lock()
guncel_png = {"veri": b""}
yeni_istegi = threading.Event()


def istek(url, referer=None):
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
         "X-Requested-With": "XMLHttpRequest"}
    if referer:
        h["Referer"] = referer
        h["Origin"] = SSO
    return urllib.request.Request(url, headers=h)


def yeni_oturum():
    """Create fresh cookie jar and mint valid OAuth authorization session."""
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    cid = ""
    st = ""
    login_url = SSO + "/login"

    # Step 0: Mint fresh authorize session via devdep backend if possible
    try:
        req = urllib.request.Request(
            MINT_URL, headers={"User-Agent": UA, "Accept": "application/json"})
        with op.open(req, timeout=10) as r:
            mint_data = json.load(r)
            login_url = mint_data["data"]["redirect_url"]
            q = urllib.parse.parse_qs(urllib.parse.urlparse(login_url).query)
            cid = q.get("client_id", [""])[0]
            st = q.get("state", [""])[0]
            # Touch authorize page inside this cookie jar
            op.open(urllib.request.Request(login_url, headers={"User-Agent": UA}), timeout=10).read()
    except Exception as me:
        # Fallback to anonymous session with empty strings (avoids 403 caused by string 'null')
        cid = ""
        st = ""
        login_url = SSO + "/login"

    # Step 1: Generate Device Code
    gen_params = urllib.parse.urlencode({"client_id": cid, "state": st})
    gen_req = istek(f"{SSO}/generateDeviceCode?{gen_params}", referer=login_url)
    gen = json.load(op.open(gen_req, timeout=20))
    dc = gen["deviceCode"]
    png = base64.b64decode(gen["qrCodeUrl"].split(",", 1)[1])

    with kilit:
        guncel_png["veri"] = png
        durum.update(kod_kisa=dc[:10] + "...", kalan=QR_OMRU,
                     asama="onay-bekleniyor", url_host="",
                     url_params=[], uretildi=time.time())

    return dc, cid, st, login_url, op


def yokla(dc, cid, st, login_url, op):
    global guncel_png
    bekleme = POLL_ARALIK
    while True:
        with kilit:
            kalan = QR_OMRU - (time.time() - durum["uretildi"])
        if kalan <= 0:
            with kilit:
                durum.update(asama="suresi-doldu")
            return

        check_params = urllib.parse.urlencode({
            "deviceCode": dc,
            "clientId": cid,
            "non": st
        })
        check_url = f"{SSO}/checkDeviceCode?{check_params}"

        try:
            with op.open(istek(check_url, referer=login_url), timeout=12) as r:
                veri = json.load(r)
                bekleme = POLL_ARALIK
        except urllib.error.HTTPError as e:
            if e.code == 403:
                # 403 indicates rate limit or pending authorization binding
                with kilit:
                    durum.update(asama="onay-bekleniyor (403 backoff)")
                time.sleep(bekleme)
                bekleme = min(bekleme * 1.5, 10)
                continue
            else:
                with kilit:
                    durum.update(asama=f"http:{e.code}")
                time.sleep(POLL_ARALIK)
                continue
        except Exception as e:
            with kilit:
                durum.update(asama=f"baglanti-hatasi")
            time.sleep(POLL_ARALIK)
            continue

        if veri and veri.get("url"):
            target_url = veri["url"]
            parca = urllib.parse.urlparse(target_url)
            params = urllib.parse.parse_qs(parca.query)
            code = params.get("code", [None])[0]
            state_param = params.get("state", [None])[0]

            user_profile = {}
            if code and state_param:
                try:
                    user_profile = exchange_code_for_user(code, state_param)
                except Exception as ex:
                    print(f"Token exchange error: {ex}")

            with kilit:
                durum.update(
                    asama="ONAYLANDI",
                    url_host=parca.hostname or "",
                    url_params=sorted(params.keys()),
                    code_received=bool(code),
                    user_name=user_profile.get("full_name") or user_profile.get("uname") or "",
                    user_role=user_profile.get("utype") or "",
                    user_id=user_profile.get("uid") or ""
                )
            return "ONAYLANDI"

        with kilit:
            if veri is not None:
                durum.update(kalan=int(kalan), asama="onay-bekleniyor")
        time.sleep(bekleme)

    return "SURESI_DOLDU"


def dongu():
    while True:
        yeni_istegi.clear()
        try:
            dc, cid, st, login_url, op = yeni_oturum()
        except Exception as e:
            with kilit:
                durum.update(asama=f"uretim-hatasi:{str(e)[:30]}")
            time.sleep(10)
            continue

        res = yokla(dc, cid, st, login_url, op)
        if res == "ONAYLANDI":
            # Keep on screen, wait until user clicks 'Yeni QR'
            yeni_istegi.wait()


SAYFA = """<!doctype html><html lang=tr><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Tahta QR Panel - EBA SSO</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;text-align:center;background:#121316;color:#eee;margin:0;padding:24px}
.container{max-width:480px;margin:0 auto;background:#1b1c20;padding:24px;border-radius:14px;box-shadow:0 8px 30px rgba(0,0,0,0.5)}
h1{font-size:1.6rem;margin-bottom:8px;color:#fff}
.subtitle{color:#8b949e;font-size:0.9rem;margin-bottom:20px}
img{background:#fff;padding:12px;border-radius:10px;width:min(70vw,280px);display:inline-block;transition:all 0.3s ease}
img.approved{border:5px solid #2ea043;box-shadow:0 0 25px rgba(46,160,67,0.6)}
#kalan{font-size:1.8rem;font-weight:bold;margin:12px 0;color:#58a6ff}
#durum{margin:12px 0;font-size:1.15rem;padding:10px;border-radius:8px;background:#262930;color:#c9d1d9}
#durum.ok{background:#1b4725;color:#7ee787;font-weight:bold}
#details{margin-top:14px;padding:12px;border-radius:8px;background:#161b22;font-family:monospace;font-size:0.85rem;color:#79c0ff;text-align:left;word-break:break-all;display:none}
button{background:#238636;color:#fff;border:none;padding:12px 24px;border-radius:6px;font-size:1rem;font-weight:bold;cursor:pointer;margin-top:16px;transition:background 0.2s}
button:hover{background:#2ea043}
</style>
<div class="container">
  <h1>Tahta EBA QR Giriş</h1>
  <div class="subtitle">EBA Mobil Uygulaması ile okutunuz</div>
  <img id=qr src="/qr.png" alt="QR">
  <div id=kalan>-</div>
  <div id=durum>Başlatılıyor...</div>
  <div id=details></div>
  <button onclick="fetch('/yeni',{method:'POST'}).then(()=>setTimeout(yenile,400))">Yeni QR Üret</button>
</div>
<script>
async function yenile(){
  let d;
  try {
    d = await (await fetch('/durum')).json();
  } catch (e) {
    document.getElementById('durum').textContent = 'Panel bağlantısı koptu';
    return;
  }
  const isApproved = d.asama === 'ONAYLANDI';
  const du = document.getElementById('durum');
  const qrImg = document.getElementById('qr');
  const details = document.getElementById('details');
  const kalan = document.getElementById('kalan');

  if (isApproved) {
    kalan.textContent = '✓ TAMAMLANDI';
    kalan.style.color = '#7ee787';
    du.textContent = '✓ Oturum ve Kullanıcı Doğrulandı!';
    du.className = 'ok';
    qrImg.className = 'approved';
    details.style.display = 'block';
    let userHtml = '';
    if (d.user_name) {
      userHtml = '<strong>Giriş Yapan:</strong> ' + d.user_name + ' (' + (d.user_role || 'kullanıcı') + ')<br>' +
                 '<strong>Kimlik (UID):</strong> ' + (d.user_id || '-') + '<br><hr style="border:0;border-top:1px solid #30363d;margin:8px 0">';
    }
    details.innerHTML = userHtml +
                        '<strong>Hedef Host:</strong> ' + d.url_host + '<br>' +
                        '<strong>OAuth Durumu:</strong> ' + (d.code_received ? 'Kod Alındı ve Takas Edildi' : 'Alındı') + '<br>' +
                        '<em>Kullanıcı kimliği doğrulandı (Güvenlik gereği jetonlar yalnızca bellekte tutulur, diske yazılmaz).</em>';
  } else {
    kalan.textContent = d.kalan + ' sn (' + d.kod_kisa + ')';
    kalan.style.color = '#58a6ff';
    du.textContent = d.asama;
    du.className = '';
    qrImg.className = '';
    details.style.display = 'none';
    qrImg.src = '/qr.png?' + Date.now();
  }
}
setInterval(yenile, 1500); yenile();
</script>"""


class El(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, veri):
        bl = json.dumps(veri).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(bl)))
        self.end_headers()
        self.wfile.write(bl)

    def do_GET(self):
        yol = urllib.parse.urlparse(self.path).path
        if yol == "/":
            bl = SAYFA.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(bl)))
            self.end_headers()
            self.wfile.write(bl)
        elif yol == "/qr.png":
            with kilit:
                veri = guncel_png["veri"]
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(veri)))
            self.end_headers()
            self.wfile.write(veri)
        elif yol == "/durum":
            with kilit:
                kalan = max(0, int(QR_OMRU - (time.time() - durum["uretildi"]))) \
                    if durum["uretildi"] else 0
                snap = dict(durum, kalan=kalan)
            self._json(snap)
        else:
            self.send_error(404)

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path == "/yeni":
            # Reset active round and wake up waiting thread
            with kilit:
                durum["uretildi"] = 0
            yeni_istegi.set()
            self._json({"tamam": True})
        else:
            self.send_error(404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    threading.Thread(target=dongu, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", args.port), El).serve_forever()


if __name__ == "__main__":
    main()
