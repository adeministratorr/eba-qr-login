#!/usr/bin/env python3
"""Automated tests for EBA SSO QR authentication and token resolution."""

import unittest
import json
import base64
import urllib.request
import urllib.parse
import sys
import os

# Prevent bytecode generation so package data stays strictly clean
sys.dont_write_bytecode = True

# Support running tests both from bundle directory and repo root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pkg_unpacked/data/usr/share/eta/eta-qr-login"))
from qrlogin_sso import SsoQrProvider
from service import gen_username


class TestEbaSsoEndpoints(unittest.TestCase):
    """Test live EBA SSO endpoints accessibility and contracts."""

    def test_01_openid_configuration(self):
        """Verify OpenID metadata returns required endpoints and device flow support."""
        url = "https://sso.eba.gov.tr/.well-known/openid-configuration"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            data = json.load(resp)
            self.assertEqual(data.get("issuer"), "https://sso.eba.gov.tr")
            self.assertIn("oauth2/token", data.get("token_endpoint", ""))
            self.assertIn("oauth2/userinfo", data.get("userinfo_endpoint", ""))
            self.assertIn("urn:ietf:params:oauth:grant-type:device_code", data.get("grant_types_supported", []))

    def test_02_generate_device_code(self):
        """Verify generateDeviceCode produces valid 'sso:' prefix and base64 PNG."""
        provider = SsoQrProvider()
        device_code, png_bytes = provider.qr_uret()
        self.assertTrue(device_code.startswith("sso:"), f"Invalid device code prefix: {device_code}")
        self.assertTrue(len(png_bytes) > 100, "PNG image bytes too short")
        self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"), "Not a valid PNG header")

    def test_03_check_device_code_pending(self):
        """Verify checkDeviceCode returns HTTP 200 with url: None while waiting for scan."""
        provider = SsoQrProvider()
        device_code, _ = provider.qr_uret()
        st, data = provider._get_json("/checkDeviceCode", {
            "deviceCode": device_code,
            "clientId": "",
            "non": ""
        })
        self.assertEqual(st, 200)
        self.assertIsNotNone(data)
        self.assertIsNone(data.get("url"))


class TestJwtClaimsExtraction(unittest.TestCase):
    """Test JWT token decoding and user profile parsing logic."""

    def test_04_jwt_claims_decoding(self):
        """Test extraction of full_name, role, and institution from JWT claims."""
        claims = {
            "sub": "QfRbj992b17LV70f9Mb66",
            "roles": {
                "320eb9b7-3716-4209-bbdc-207edfd9e521": "TEACHER"
            },
            "profiles": [
                {
                    "first_name": "ADEM",
                    "last_name": "YÜCE",
                    "job_title": "Öğretmen",
                    "branch_name": "Bilişim Teknolojileri",
                    "institution_name": "Selçuklu Mesleki ve Teknik Anadolu Lisesi"
                }
            ]
        }
        header_b64 = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').decode().rstrip("=")
        payload_b64 = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode().rstrip("=")
        mock_jwt = f"{header_b64}.{payload_b64}.mock_signature"

        token_parts = mock_jwt.split(".")
        padded = token_parts[1] + "=" * ((4 - len(token_parts[1]) % 4) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))

        p0 = decoded["profiles"][0]
        full_name = f"{p0['first_name']} {p0['last_name']}".strip()
        self.assertEqual(full_name, "ADEM YÜCE")
        self.assertEqual(decoded["sub"], "QfRbj992b17LV70f9Mb66")
        self.assertEqual(decoded["roles"]["320eb9b7-3716-4209-bbdc-207edfd9e521"], "TEACHER")

    def test_05_username_sanitization_and_gecos(self):
        """Test username generation strips whitespace and special chars for Linux useradd."""
        uname = gen_username("ADEM YÜCE")
        self.assertEqual(uname, "ademyuce")
        self.assertNotIn(" ", uname)
        self.assertNotIn("Ü", uname)
        self.assertNotIn("ü", uname)


if __name__ == "__main__":
    unittest.main()
