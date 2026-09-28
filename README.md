# Pardus ETAP `eta-qr-login` - EBA SSO Karekod Giriş Uyumluluk Yaması (v0.2.6.1)

> **Kime:** Pardus ETAP Geliştirici Ekibi & Paket Yöneticisi  
> **Konu:** Yeni EBA Mobil (v4.0.7+) Uygulaması ile Uyumlu Karekod Giriş Entegrasyonu  
> **Hazırlayan:** Adem YÜCE (Selçuklu Mesleki ve Teknik Anadolu Lisesi)  
> **Tarih:** Eylül 2026  

---

> **📌 Versiyonlama Notu (v0.2.6.1):**  
> Resmi Pardus ETAP 23 deposundaki mevcut paket `0.2.6` sürümündedir. Bu yama paketi `0.2.6.1` olarak versiyonlanmıştır. Böylece hem akıllı tahtalara `dpkg -i` ile sorunsuz yükseltme yapılabilmekte, hem de Pardus ETAP ekibi ileride resmi bir `0.2.7` (veya üzeri) sürüm yayınladığında sistemlerin `apt update && apt upgrade` ile resmi sürüme otomatik ve sorunsuz geçiş yapabilmesi garanti edilmektedir.


## 📌 1. Giriş ve Sorunun Tanımı

Pardus ETAP akıllı tahtalarında kullanılan `eta-qr-login` (v0.2.6) paketi, Milli Eğitim Bakanlığı'nın kullanıma sunduğu **yeni EBA Mobil Uygulaması (v4.0.7+)** ile karekodla oturum açma işlevini yerine getirememektedir.

Akıllı tahta ekranında beliren karekod yeni mobil uygulama ile taratıldığında telefon hiçbir tepki vermemekte veya hata vermektedir. Bu çalışma; sorunun kök nedenini tespit etmek, MEB'in yeni kimlik doğrulama mimarisini tersine mühendislikle çözümlemek ve `eta-qr-login` paketini geriye dönük uyumlu, güvenli ve harici bağımlılık gerektirmeden güncellemek amacıyla hazırlanmıştır.

---

## 🔍 2. Kök Neden Analizi (Root Cause Analysis)

Eski ve yeni sistem arasındaki teknik farklar ve tespit edilen bulgular:

### A. Karekod Formatı ve Mobil Filtre Değişikliği
* **Eski Sistem (`0.2.6`):** Tahta `service.py`, `PRDS-<rastgele-uuid>` formatında bir metin üretip karekoda dönüştürüyordu.
* **Yeni EBA Mobil (v4.0.7+):** Mobil uygulama (Google ML Kit Barcode Scanning) karekodu okuduğunda dahili bir filtre uygulamaktadır:
  - Kod içeriği `sso:` ile **başlamıyorsa** veya içinde `=` karakteri **bulunmuyorsa**, uygulama bunu geçersiz kabul etmekte ve **hiçbir ağ isteği atmadan** karekodu anında reddetmektedir.
  - Bu nedenle eski `PRDS-` formatındaki kodlar telefon tarafından doğrudan elenmekteydi.

### B. Onay Mekanizması & Röle Sunucusu Değişikliği
* **Eski Sistem (`0.2.6`):** Tahta `wss://qr-etap.eba.gov.tr` websocket sunucusuna bağlanıp beklemekte; telefon ise eski `https://.../api/v1/eba-callback/<uuid>` ucuna HTTP POST atarak websocket üzerinden tahtaya onay düşürmekteydi.
* **Yeni EBA Mobil (v4.0.7+):** MEB altyapısı bu eski callback ucunu tamamen devreden çıkarmıştır. Yeni mobil uygulama, taranan karekodu doğrudan `https://sso.eba.gov.tr/verifyDevice?code=<device_code>` adresine göndererek MEB SSO üzerinde cihazı yetkilendirmektedir.

---

## 🔄 3. Eskiye Göre Neler Değişti?

Aşağıdaki tablo, `0.2.6` ile bu yama (`0.2.6.1`) arasındaki mimari ve teknik farkları özetlemektedir:

| Özellik / Katman | Eski Durum (`v0.2.6`) | Yeni Durum (`v0.2.6.1`) |
|---|---|---|
| **Protokol** | Özel WebSocket Rölesi (`wss://qr-etap.eba.gov.tr`) | Standart HTTPS OAuth2 Device Authorization Flow (`sso.eba.gov.tr`) |
| **Karekod Formatı** | `PRDS-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` | `sso:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` (Resmi EBA SSO Cihaz Kodu) |
| **Karekod Üretimi** | Tahta yerelinde UUID üretilip sunucuya bildiriliyordu | EBA SSO sunucusundan oturuma özel tekil cihaz kodu çekilmektedir |
| **Onay Yakalama** | WebSocket `on_message` dinlemesi | EBA SSO `/checkDeviceCode` yoklaması (Exponential Backoff korumalı) |
| **Kimlik Çözümleme** | Sadece UID ve kullanıcı adı bilgisi | OAuth2 `code` takası ile RS256 JWT Access Token çözümlenerek Ad, Soyad, TCKN/UID, Rol (`TEACHER`) ve kurum bilgisi elde edilir |
| **Harici Bağımlılık** | `python3-websockets` (WebSocket artık kullanılmıyor) | **Ek bağımlılık YOK**. Tamamen Python standart kütüphanesi (`urllib.request`, `json`, `base64`, `hashlib`) ile yazılmıştır |
| **LightDM & Greeter Uyumu** | `/run/etap/qr-trigger` ve `/var/lib/lightdm/ebaqr` | **%100 Aynen Korundu**. `qrwidget.py` veya greeter'da tek bir satır kod değiştirmeye gerek yoktur |
| **Kullanıcı Oluşturma** | `eta-usb-login` (`create_user`, `allow_user`, `lightdm_trigger`) | **%100 Aynen Korundu**. Aynı Linux kullanıcı açma ve oturum tetikleme mantığı çalışır |
| **Güvenlik & Gizlilik** | Bellek içi | **Bellek içi (In-Memory)**. Diske hiçbir oturum dosyası, parola veya jeton yazılmaz |

---

## 🛠️ 4. Yapılan Kod Değişiklikleri (`service.py`)

1. **Eski WebSocket Kodları Temizlendi:** Artık çalışmayan `wss://qr-etap.eba.gov.tr` bağlantı döngüsü ve `websockets` kütüphane çağrıları kaldırıldı.
2. **`mint_sso_session()` Eklendi:** EBA Asistan SSO oturumu üzerinden tahta için geçerli ve resmi bir `deviceCode` üretir.
3. **`poll_sso_approval()` Eklendi:** 55 saniyelik zaman aşımı süresince güvenli aralıklarla cihaz onayını yoklar. MEB sunucularını yormamak ve hız sınırına (HTTP 403) takılmamak için **exponential backoff** uygulanmıştır.
4. **`exchange_code_for_user()` Eklendi:** Telefondan gelen onayı tek kullanımlık OAuth2 yetkilendirme kodu ile takas eder, JWT içindeki `profiles` dizisinden öğretmenin tam adını ve soyadını çözer.
5. **Unix Soket Arayüzü Birebir Korundu:** `UnixSocketService` ve `send_lightdm` işlevleri aynen tutulduğu için `qrlogin.py` ve `qrwidget.py` bileşenleri yeni karekod sistemini şeffaf olarak kullanır.

---

## 🧪 5. Test ve Saha Doğrulama Sonuçları

Geliştirilen kodlar hem otomatik testlerle hem de sahada gerçek akıllı tahta ve cep telefonuyla test edilmiştir:

### A. Otomatik Test Çıktısı (`test_sso_flow.py`):
```text
test_01_openid_configuration: OK (OpenID metadata & device flow contract verified)
test_02_generate_device_code: OK (sso: prefix & valid PNG header verified)
test_03_check_device_code_pending: OK (HTTP 200 with url: None verified)
test_04_jwt_claims_decoding: OK (Profiles array, full name & teacher role verified)

Ran 4 tests in 0.292s -> OK
```

### B. Canlı EBA Mobil Uygulaması Doğrulaması:
- **Test Cihazı:** EBA Mobil v4.0.7 (Android / iOS)
- **Doğrulanan Kullanıcı:** Adem YÜCE
- **Giriş Rolü:** `TEACHER` (Öğretmen)
- **Kurum:** Selçuklu Mesleki ve Teknik Anadolu Lisesi (KONYA)
- **Sonuç:** Karekod anında algılandı, telefonda onay ekranı açıldı, onay verildikten 1 saniye sonra tahtada oturum başarıyla açıldı.

---

## 📦 6. Depo İçeriği ve Dosya Yapısı

Bu depo, geliştirici ekibin incelemesi ve doğrudan Pardus ETAP depolarına aktarabilmesi için hazırlanmıştır:

| Dosya | Açıklama |
|---|---|
| `README.md` | Teknik dokümantasyon, kök neden analizi ve saha doğrulama raporu |
| `service.py` | Yeni EBA SSO motorunu içeren nihai Python servis dosyası |
| `unix_socket_service.py` | Greeter ve LightDM Unix soket arayüzü |
| `qrlogin_sso.py` | EBA SSO Provider, OAuth2 device authorization ve kimlik çözümleyici modül |
| `service_eba_sso.patch` | Orijinal `0.2.6` sürümüne karşı oluşturulmuş `git diff / patch` dosyası |
| `service_original_0.2.6.py` | Referans orijinal `0.2.6` servis kodu |
| `test_sso_flow.py` | Akışı doğrulayan otomatik birim ve sözleşme testleri |
| `repack_deb.py` | Temiz, tekrarlanabilir Debian paketi oluşturan derleme aracı |
| `build_package.sh` | Tek tıkla paketi derleyen kabuk betiği |
| `tahta_qr_panel.py` | Akıllı tahta canlı test ve teşhis web paneli |
| `eta_qr_diagnostic.sh` | Akıllı tahtada sorun tespiti yapan tanı betiği |

> **📥 Hazır Debian Paketi (`.deb`):**  
> Test edilmiş ve imzaya hazır Debian paketi (`eta-qr-login_0.2.6.1_all.deb`) ve kaynak arşivleri deponun **[Releases](https://github.com/adeministratorr/eba-qr-login/releases)** sekmesinden indirilebilir.

---

## 🚀 7. Nasıl Uygulanır?

### Seçenek A: Yamayı (Patch) Kaynak Koda Uygulamak
Mevcut `eta-qr-login` git deposunda `service.py` dosyasına yamayı uygulamak için:
```bash
patch -p1 < service_eba_sso.patch
```

### Seçenek B: Hazır Paketi Derlemek veya İndirip Kurmak
Paketi sıfırdan derlemek için:
```bash
./build_package.sh
```

Hazır derlenmiş `.deb` paketini tahtaya kurmak için:
```bash
sudo dpkg -i eta-qr-login_0.2.6.1_all.deb
sudo systemctl restart ebaqr
systemctl status ebaqr
```

---

*Pardus ETAP projesine ve açık kaynak eğitim ekosistemine katkı sunabilmesi dileğiyle.*


