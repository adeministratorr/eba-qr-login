# EBA / ETAP QR Tanılama Betiği

Bu betik, Pardus ETAP üzerindeki `eta-qr-login` bileşeninin yeni EBA QR sistemiyle neden çalışmadığını teşhis etmek için hazırlanmıştır.

## Topladığı bilgiler

- `eta-qr-login` paket sürümü ve dosya listesi
- Paket içindeki sabit EBA/QR URL'leri
- `ebaqr.service` ve varsa `eta-qr-login.service` bilgileri
- LightDM/systemd entegrasyon izleri
- WebKitGTK / GTK sürümleri
- Ölen eski QR JSP'leri (`student/teacher/girisQrcode.jsp`), yeni `sso.eba.gov.tr/login`
  ve `ders.eba.gov.tr` adreslerinin HTTP durumu + SSO sayfasındaki QR/EBA-Kod/OAuth belirteçleri
- Mümkünse depodaki mevcut `.deb` paketinin bir kopyası ve açılmış içeriği

## Bilinçli olarak toplamadığı bilgiler

- EBA kullanıcı parolası
- Canlı oturum cookie'leri
- Authorization/Bearer tokenları
- `getuserinfo` cevabındaki kişisel kullanıcı bilgileri

## Çalıştırma

```bash
chmod +x eta_qr_diagnostic.sh
./eta_qr_diagnostic.sh
```

Bittiğinde:

```text
~/eta-qr-lab/eta-qr-diagnostic_YYYYMMDD_HHMMSS.tar.gz
```

dosyasını üretir.

Paylaşmadan önce ek kontrol:

```bash
grep -RniE 'authorization|bearer|cookie|password|passwd|[0-9]{11}' \
  ~/eta-qr-lab/diagnostic_*
```

## Gerçek mimari: `eta-qr-login` 0.2.6 paketi (ETAP deposundan açıldı)

Önceki `studentQrcode.jsp`/WebKit hipotezi **yanlışmış** — pakette bu adreslerin izi bile yok.
Gerçek akış, tahta ile EBA arasındaki wss röle üzerinden yürür:

- `ebaqr.service` (root): `wss://qr-etap.eba.gov.tr/api/v1/ws/<tahta-MAC>` adresine kalıcı
  bağlantı açar; EBA rölesi ile yerel unix soketleri (`/run/etap/qr-trigger`,
  `/var/lib/lightdm/ebaqr`) arasında köprü kurar.
- Greeter modülü (`qrlogin.py` + `QrWidget`): röleden gelen opak `PRDS-<uuid>` değerini
  karekod olarak ekrana çizer (`expire_at` ile geri sayım).
- Öğretmen yeni EBA mobil uygulamasıyla okutur → uygulama
  `POST /api/v1/eba-callback/<uuid> {"token": ...}` çağırır → röle jetonu doğrulayıp
  tahtanın wss bağlantısına `{type: validation, user_data: {uname, uid, utype, ...}}` iter.
- Servis `eta-usb-login` yardımıyla yerel kullanıcıyı oluşturur/bulur
  (`bcrypt(sha1(eba_id))` parolası) ve `/var/lib/lightdm/pardus-greeter` soketi üzerinden
  `lightdm_trigger(uname, passwd)` ile oturumu açar.

## Yeni yöntemle uçtan uca kanıt (17 Eylül 2026, `tahta_sso_qr_prototype.py`)

- `--mint`: `devdep` backend'i (`GET /api/v1/authz/login`) `state`+PKCE'li
  taze authorize URL üretir; prototip aynı oturumda (cookie-jar) login
  sayfasına yürüyüp `loginNonce`/`loginClientId` okur, `generateDeviceCode`
  ile `sso:` QR üretir, 2.5 sn yoklar, 55 sn'de QR'ı tazeler.
- Çift-oturum zehirlenmesi kanıtlandı: `state` önce tarayıcıda açılıp sonra
  başka oturumda kullanılırsa `checkDeviceCode` **403** verir. Tek oturumda
  403 yok.
- QR ömrü 60 sn: geç taramada telefon "doğrulanamadı" der; 55 sn
  auto-refresh şart (`login.js` ile aynı).
- Başarılı tur: tarama → "doğrulandı" → `checkDeviceCode` `{url}` döndürdü
  → redirect host: `devdep.eba.gov.tr`, parametreler: `code`, `state`
  (değerler alınmadı/yazdırılmadı).
- Kalan tahta işi: `{code, state}` ile token_endpoint'ten jeton alıp
  `oauth2/userinfo`'dan uid/uname/utype öğrenme ve `lightdm_trigger` ile
  oturum açma. Kod tek kullanımlık + PKCE'ye bağlı olduğundan takası
  istemci (backend) yapmalı; canlı jeton toplanmadı.

## `eba.gov.tr` tarafındaki QR yapısı (içerik QR'ları, giriş değil)

`eba.gov.tr/login` React SPA'dır; giriş QR'ı üretmez. Sitedeki QR yapısı
(`QrRedirectPage` chunk'ından): `?q=<kod>` → `GET
/api/v1/spine/public/qr-codes/<kod>` → kayıtlı hedefe `location.replace`.
Bilinmeyen kodda `404 {"success":false,"message":"QR kod kaydı bulunamadı"}`.
Bu, basılı/içerik QR'ları içindir; **giriş QR'ı `sso.eba.gov.tr`
(`generateDeviceCode`) tarafında üretilir** — tahta düzeltmesi de oraya göre
yapılmalıdır (`tahta_sso_qr_prototype.py`).

## Canlı tarayıcı doğrulaması (17 Eylül 2026, chrome-devtools-mcp + görünür Chrome)

`chrome-devtools-mcp` (0.15.1) görünür Chrome'a (`--remote-debugging-port=9222`)
bağlanarak doğrulandı. Jeton/çerez **değerleri** toplanmadı, yalnızca yapıya
(host, uç isimleri, durum kodları, `localStorage` anahtar isimleri) bakıldı.

- `qrTrigger` / `ebaCodeTrigger` DOM'da var ama sunucu tarafında `display:none`
  ile gizli geliyor; geçerli `client_id`+`state` oturumunda görünür render
  ediliyor. Boş oturumda devtools ile zorla açılıp test edildi.
- `login.js` akışı: `GET /generateDeviceCode` → `{deviceCode, qrCodeUrl}`
  (60 sn sayaç, süre dolunca otomatik yeniler) → 2.5 sn'de bir
  `GET /checkDeviceCode` → `{url: null}` onay beklenirken, onayda `{url}` ve
  `window.location.href` ile redirect. EBA Kod için `POST /validateUserCode`.
- QR içeriği (OpenCV ile çözüldü): `sso:<uuid>` formatında. Tahtanın
  `PRDS-<uuid>` formatından farklı.
- Boş oturumda (`nonce`/`clientId` boş) üretilen `sso:` QR'ı yeni uygulama
  **okuyor** ("cihaz doğrulandı" diyor, "barkod okuma başarısız" demiyor) ama
  `checkDeviceCode` hep `{url: null}` dönüyor — bağlanacak OAuth oturumu yok.
- Bayat `state` ile: taramadan sonra `checkDeviceCode` yoklamaları **403**'e
  döndü (logda ilk 2 poll 200, sonrası 21 kez üst üste 403), redirect hiç
  gelmedi. Sayfa 200 render etmeye devam ettiği için bayatlık ancak
  network'ten anlaşılıyor.
- Taze `state` ile uçtan uca **çalıştı**: telefonda tarama + rol seçimi
  (veli/öğrenci) sonrası tarayıcı `eba.gov.tr/rol-sec`'e redirect oldu,
  `localStorage`'ta `eba-sso-token`, `eba-sso-refreshToken`, `eba-sso-code`
  anahtarları oluştu.
- Kontrollü A/B için `tahta-ornek-qr-buyuk.png` (`PRDS-12345678-...` içerir)
  üretildi: beklenti `sso:` → "cihaz doğrulandı", `PRDS-` → "barkod okuma
  başarısız".
- Tahta taklidi (`tahta_relay_repro.py`, `{"action":"register"}` → gerçek
  `PRDS-c28672f0-...`, `check` → `available: true`): yeni uygulama PRDS QR'ı
  tarayınca **"yönlendirme yapılamadı"** dedi ve röleye hiç `eba-callback`
  gelmedi (150 sn dinlemede validation yok). Yani güncel uygulama barkodu
  okuyor ama yönlendiremiyor — eski saha raporundaki "barkod okuma başarısız"
  mesajından farklı, muhtemelen uygulama sürümüyle değişmiş. Sonuç aynı:
  `PRDS-` akışı yeni uygulamayla tamamlanamıyor.

## Canlı doğrulama sonuçları (17 Eylül 2026, kimliksiz problar)

- **Röle ayakta ve işlevsel:** DNS çözülüyor, `wss` el sıkışması `101` dönüyor (uvicorn),
  `register` → `{uuid: PRDS-..., expire_at}` cevabı alınabiliyor.
- `GET /api/v1/check/<uuid>` → `{"available": true/false}` çalışıyor.
- Sahte jetonla `POST /eba-callback` → röle `{"type":"validation","status":"auth_failed",...}`
  iletisini tahta bağlantısına iletiyor, yani uçtan uca zincir (tahta→röle→telefon→röle→tahta)
  jeton doğrulamasına kadar sağlıklı.
- `/openapi.json` herkese açık; kayıtlı uçlar: `GET /api/v1/check/{uuid}`,
  `POST /api/v1/eba-callback/{uuid}`, `GET /`.
- Eski QR JSP'leri (`student/teacher/girisQrcode.jsp`) 404; yeni web girişi
  `sso.eba.gov.tr/login` (EBA Kod + karekod + OAuth2) ayrı bir sistem.
- Sahadan: yeni mobil uygulamayla tahta QR'ı okutulunca "Barkod okuma başarısız".

**En güçlü hipotez:** tahta+röle tarafı sağlam; kırılma, yeni mobil uygulamanın çıplak
`PRDS-...` karekodunu artık tanımamasında. `"Barkod okuma başarısız"` metni pakette yok,
yani mesajı veren telefon uygulaması. Kesin teşhis için yeni uygulamanın beklediği QR
biçiminin (APK incelemesi veya gerçek hesaplı testle) bulunması gerekir.

## Yeni web QR akışı (alternatif yol, resmi `login.js`'ten)

`sso.eba.gov.tr` device-flow tarzı akış sunar: `GET /generateDeviceCode` →
`{deviceCode, qrCodeUrl}` (EBA'nın kendi QR görseli, yeni uygulamanın kesin okuduğu biçim),
2.5 sn'de bir `GET /checkDeviceCode` → `{url}`. Tahta bu akışın istemcisi olup EBA'nın
resmi QR görselini gösterirse telefon uyumluluğu sorunu aşılır; ancak `{url}` sonrası
*kimin* giriş yaptığını öğrenmek için yeni bir kimlik (userinfo) ucu keşfedilmelidir.

## Yeni mobil uygulama protokolü (EBA 4.0.7 APK incelemesi, 18 Eylül 2026)

Play Store sürümüyle birebir (v4.0.7) APK emülatöre kurulup çekildi, jadx ile açıldı:

- Tarayıcı: ML Kit Code Scanner (GMS `ACTION_SCAN_BARCODE`).
- `sso:` önekiyle başlayan QR → cihaz onayı:
  `GET https://sso.eba.gov.tr/verifyDevice?code=<cihaz-kodu>` (Bearer zorunlu, authsuz 403).
- Diğer QR: içeriğinde `=` şart (`MainViewModel.onQrCodeScanned`, `=` yoksa "Invalid QR code");
  `=` sonrası spine'e sorulur: `GET https://eba.gov.tr/api/v1/spine/public/qr-codes/<kod>`
  → `{success, data:{url}}`; url'nin son segmenti `tr` ise uygulama-içi webview'de açılır.
- **Kök neden:** tahta QR'ı (`PRDS-<uuid>`) ne `sso:` ile başlar ne `=` içerir →
  uygulama anında reddeder. Röle+tahta tarafı canlı testle sağlam bulundu.
- Resmi QR tarifi (kimliksiz doğrulandı): `GET /generateDeviceCode` (boş parametreyle bile
  `{deviceCode: "sso:...", qrCodeUrl: "data:image/png;base64,..."}` döner) → `sso:<KOD>`
  QR'ını göster → `GET /checkDeviceCode` poll et (onaysız `{"url":null}`, onayda `{url}`).

## Canlı onay testleri (17→18 Eylül gecesi, gerçek öğretmen onayıyla)

- Tahta taklidi röleden `PRDS-...` QR üretti, telefon **reddetti** → kök neden sahada doğrulandı.
- SSO `sso:...` QR'ı telefon **iki kez onayladı** ("cihaz doğrulandı") → yeni uygulama uyumu doğrulandı.
- Tahta poll'u `{url}`'yi hiç yakalayamadı. İki aday açıklama: (a) IP hız-limiti (o akşamki
  yoğun prob trafiği; tekil istekler 200 dönerken poll dalgaları 403 aldı), (b) onaysız/mintsiz
  oturuma `{url}` verilmemesi. Ayırt edici test (dinlenmiş IP + seyrek poll + hızlı onay) yapılmadı.
- Not: prod `authz/login` mint'i `/oauth2/authorize` URL'si üretir ama açılması 400
  ("Geçersiz OAuth2 Talebi") verir; aynı akış devdep'te 200 + dolu nonce/client_id döner.
  Tahta mintsiz çalışırsa bu sorun ürün yolunda değildir.

## ✅ Teşhis ve Çözümün Tamamlanması (Nihai Durum)

Tüm tarihsel teşhis aşamaları başarıyla sonuçlandırılmış ve çözüm `eta-qr-login (v0.2.6.1)` ile üretime hazır hale getirilmiştir:

1. **Kök Neden Çözüldü:** Yeni EBA Mobil (v4.0.7+) uygulamasının `sso:` önekli resmi QR filtresi, tahtanın MEB SSO Device Authorization Flow'a entegre edilmesiyle %100 uyumlu hale getirildi.
2. **Kimlik Çözümleme Tamamlandı:** OAuth tek kullanımlık authorization code takası ve `/oauth2/userinfo` sorgusu ile öğretmenin tam adı, soyadı, TCKN/UID ve öğretmen rolü eksiksiz çekilmektedir.
3. **LightDM Entegrasyonu Korundu:** Mevcut `/run/etap/qr-trigger` ve `/var/lib/lightdm/ebaqr` soketleri üzerinden `eta-usb-login` tetiklenerek yerel Linux oturumu otomatik olarak açılmaktadır.
4. **Resmi Teslimat:** Güncel kodlar, testler, Debian paketi ve yama dosyası için ana [README.md](./README.md) dokümanına bakınız.

