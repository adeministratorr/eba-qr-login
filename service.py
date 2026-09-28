#!/usr/bin/env python3
"""EBA SSO QR Login Service for Pardus ETAP (replaces legacy wss relay with EBA SSO)."""

import base64
import hashlib
import http.cookiejar
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
try:
    from passlib.hash import bcrypt
except ImportError:
    class _MockBcrypt:
        @staticmethod
        def hash(secret):
            return f"$2b$12$mockhash.{secret[:8]}"
    bcrypt = _MockBcrypt()

try:
    from unix_socket_service import UnixSocketService
except ImportError:
    class UnixSocketService:
        def __init__(self, path):
            self.path = path
            self.event = None

        def run(self):
            pass

sys.path.insert(0, "/usr/share/eta/eta-usb-login")

try:
    # Import usb login user creation and login functions
    from user import create_user, is_valid_user, find_by_ebaid
    from pam import lightdm_trigger, allow_user
except ImportError:
    # Mock fallback for non-ETAP testing environments
    def create_user(uname, pwhash, user, eba_id):
        print(f"[mock] create_user({uname}, {user}, {eba_id})", file=sys.stderr)

    def is_valid_user(uname):
        return False

    def find_by_ebaid(eba_id):
        return None

    def lightdm_trigger(uname, passwd):
        print(f"[mock] lightdm_trigger({uname})", file=sys.stderr)

    def allow_user(uname):
        print(f"[mock] allow_user({uname})", file=sys.stderr)


try:
    os.makedirs("/run/etap", exist_ok=True)
    os.makedirs("/var/lib/eta/expire-uid", exist_ok=True)
    socket_service = UnixSocketService("/run/etap/qr-trigger")
except OSError:
    # Fallback for non-root test environments
    socket_service = UnixSocketService("/tmp/qr-trigger")

SSO = "https://sso.eba.gov.tr"
MINT_BASE = os.environ.get("TAHTA_AUTH_BASE", "https://devdep.eba.gov.tr/api")
MINT_PATH = "/v1/authz/login"
MINT_REDIRECT = os.environ.get("TAHTA_AUTH_REDIRECT", "https://devdep.eba.gov.tr/asistan/sso/giris")
MINT_URL = f"{MINT_BASE}{MINT_PATH}?redirect_uri={urllib.parse.quote(MINT_REDIRECT, safe='')}&platform=web"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

active_session = {
    "cancel_event": None,
    "lock": threading.Lock()
}


def gen_username(u):
    u = u.replace("İ", "I")
    u = u.lower()
    u = u.replace("ç", "c")
    u = u.replace("ı", "i")
    u = u.replace("ğ", "g")
    u = u.replace("ö", "o")
    u = u.replace("ş", "s")
    u = u.replace("ü", "u")
    u = u.replace(" ", "")
    return u


def find_uid(uname):
    try:
        with open("/etc/passwd", "r") as f:
            for line in f.read().split("\n"):
                cur = line.split(":")[0]
                if uname == cur:
                    return line.split(":")[2]
    except Exception:
        pass
    return None


def send_lightdm(data):
    """Send message to lightdm greeter unix socket"""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect("/var/lib/lightdm/ebaqr")
        s.send(json.dumps(data).encode())
        s.close()
    except Exception as e:
        print(f"Error sending to lightdm: {e}", file=sys.stderr)


def user_create_login(data):
    """Create local Linux user and trigger LightDM login upon EBA verification."""
    if data.get("type") == "validation" and "user_data" in data:
        display_name = data["user_data"].get("full_name") or data["user_data"].get("uname") or "EBA Kullanicisi"
        eba_id = data["user_data"]["uid"]
        raw_uname = data["user_data"].get("uname") or display_name
        uname = gen_username(raw_uname)
        eba_uname = find_by_ebaid(eba_id)
        if eba_uname:
            uname = eba_uname
        else:
            i = 0
            new_user = uname
            while is_valid_user(new_user):
                new_user = uname + str(i)
                i += 1
            uname = new_user

        passwd = hashlib.sha1(eba_id.encode("utf-8")).hexdigest()
        if is_valid_user(uname):
            print(f"Allowing existing user: {uname}", file=sys.stderr)
            allow_user(uname)
        else:
            print(f"Creating user: {uname} (display: {display_name})", file=sys.stderr)
            create_user(uname, bcrypt.hash(passwd), display_name, eba_id)
            user_uid = find_uid(uname)
            if user_uid:
                try:
                    with open(f"/var/lib/eta/expire-uid/{user_uid}", "w") as f:
                        f.write(uname)
                except Exception as ef:
                    print(f"Failed to write expire-uid: {ef}", file=sys.stderr)

        print(f"Triggering lightdm login for {uname}...", file=sys.stderr)
        lightdm_trigger(uname, passwd)


try:
    from qrlogin_sso import exchange_code_for_user
except ImportError:
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


def mint_sso_session():
    """Mint a fresh OAuth session and generate deviceCode."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    cid = ""
    st = ""
    login_url = SSO + "/login"

    try:
        req = urllib.request.Request(MINT_URL, headers={"User-Agent": UA, "Accept": "application/json"})
        with opener.open(req, timeout=10) as r:
            mint_res = json.load(r)
            login_url = mint_res["data"]["redirect_url"]
            q = urllib.parse.parse_qs(urllib.parse.urlparse(login_url).query)
            cid = q.get("client_id", [""])[0]
            st = q.get("state", [""])[0]
            opener.open(urllib.request.Request(login_url, headers={"User-Agent": UA}), timeout=10).read()
    except Exception as me:
        print(f"Mint error, fallback to anonymous: {me}", file=sys.stderr)
        cid = ""
        st = ""
        login_url = SSO + "/login"

    gen_params = urllib.parse.urlencode({"client_id": cid, "state": st})
    gen_req = urllib.request.Request(
        f"{SSO}/generateDeviceCode?{gen_params}",
        headers={"User-Agent": UA, "Accept": "application/json", "Referer": login_url}
    )
    with opener.open(gen_req, timeout=15) as gr:
        gen_data = json.load(gr)

    device_code = gen_data["deviceCode"]
    return device_code, cid, st, login_url, opener


def poll_sso_approval(dc, cid, st, login_url, opener, cancel_event):
    """Poll checkDeviceCode until user scans and approves on mobile app."""
    poll_interval = 2.5
    backoff = poll_interval
    expire_time = time.time() + 55

    while time.time() < expire_time and not cancel_event.is_set():
        params = urllib.parse.urlencode({
            "deviceCode": dc,
            "clientId": cid,
            "non": st
        })
        req = urllib.request.Request(
            f"{SSO}/checkDeviceCode?{params}",
            headers={"User-Agent": UA, "Accept": "application/json", "Referer": login_url}
        )

        try:
            with opener.open(req, timeout=10) as r:
                data = json.load(r)
                backoff = poll_interval
        except urllib.error.HTTPError as he:
            if he.code == 403:
                time.sleep(backoff)
                backoff = min(backoff * 1.5, 10)
                continue
            time.sleep(poll_interval)
            continue
        except Exception:
            time.sleep(poll_interval)
            continue

        if data and data.get("url"):
            target_url = data["url"]
            q = urllib.parse.parse_qs(urllib.parse.urlparse(target_url).query)
            code = q.get("code", [None])[0]
            state_param = q.get("state", [None])[0]
            if code and state_param:
                try:
                    user_info = exchange_code_for_user(code, state_param)
                    print(f"Successfully resolved user: {user_info.get('full_name')} ({user_info.get('uid')})", file=sys.stderr)
                    user_create_login({
                        "type": "validation",
                        "user_data": {
                            "uname": user_info["uname"],
                            "full_name": user_info["full_name"],
                            "uid": user_info["uid"],
                            "utype": user_info["utype"]
                        }
                    })
                    return
                except Exception as ex:
                    print(f"Failed to exchange code: {ex}", file=sys.stderr)
                    send_lightdm({"action": "failed", "message": "Kullanıcı doğrulama hatası"})
                    return

        time.sleep(backoff)

    if not cancel_event.is_set():
        print("SSO QR code expired, notifying greeter...", file=sys.stderr)
        send_lightdm({"action": "timeout", "sender": "ws"})


def event(data):
    """Handle requests from lightdm greeter"""
    if "sender" not in data:
        return

    action = data.get("action")
    print(f"Handling greeter event: {action}", file=sys.stderr)

    if action == "register":
        with active_session["lock"]:
            if active_session["cancel_event"]:
                active_session["cancel_event"].set()

            cancel_event = threading.Event()
            active_session["cancel_event"] = cancel_event

            try:
                dc, cid, st, login_url, opener = mint_sso_session()
                expire_at = int(time.time() + 55)
                # Send QR code UUID and expiration to greeter
                send_lightdm({
                    "uuid": dc,
                    "expire_at": expire_at,
                    "sender": "ws"
                })
                # Launch background poller
                th = threading.Thread(
                    target=poll_sso_approval,
                    args=(dc, cid, st, login_url, opener, cancel_event),
                    daemon=True
                )
                th.start()
            except Exception as e:
                print(f"Failed to initialize SSO round: {e}", file=sys.stderr)
                send_lightdm({"action": "failed", "sender": "ws"})


socket_service.event = event


def main():
    print("Starting EBA SSO QR service for Pardus ETAP...", file=sys.stderr)
    socket_thread = threading.Thread(target=socket_service.run)
    socket_thread.start()
    socket_thread.join()


if __name__ == "__main__":
    main()
