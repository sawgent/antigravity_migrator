# 🚀 Antigravity Migrator (Antigravity Taşıyıcı)

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)]()

**Antigravity Migrator**, Google Antigravity üzerinde geliştirdiğiniz projeleri, özel becerileri (skills), eklentileri (plugins), kuralları (rules) ve konuşma geçmişini (brain) yeni bir bilgisayara veya farklı bir ortama **güvenle, sürüm çakışması yaşamadan ve tek tıkla** aktarmanızı sağlayan masaüstü yardımcı aracıdır.

---

## ❓ Neden `.gemini` Klasörünü Komple Kopyalamamalıyız?

Birçok kullanıcı yeni bilgisayara geçerken `C:\Users\<Kullanıcı>\.gemini` klasörünü tamamen kopyalamayı düşünür. Ancak bu yöntem aşağıdaki ciddi sorunlara yol açar:

1. **Sürüme Özel İkili Dosyalar (Binaries):** `.gemini` altında bulunan `bin/`, `builtin/`, `lib/` ve `sandbox/` klasörleri o anki Antigravity sürümüne özel çalıştırılabilir dosyaları ve kütüphaneleri barındırır. Yeni sürüme eski ikili dosyaları yapıştırmak uygulamanın çökmesine yol açar.
2. **Kullanıcı Adı ve Mutlak Yollar (Absolute Paths):** Konfigürasyonlarda ve veritabanlarında `C:\Users\EskiKullanici\...` şeklinde yollar tutulur. Kullanıcı adı veya yol değiştiğinde bağlantılar kopar.
3. **Kilit ve Durum Dosyaları (Runtime Locks / WAL):** `daemon/`, `cache/`, `*.db-wal`, `*.db-shm` gibi çalışma anına ait geçici dosyalar yeni sistemde veritabanı kilitlenme hatalarına sebep olur.
4. **Cihaz Kimliği (`installation_id`):** Antigravity her cihaza özgü benzersiz bir kimlik üretir; bunun taşınmaması gerekir.

**Antigravity Migrator**, bu riskli dosyaları otomatik olarak filtreler ve yalnızca kullanıcı çalışmalarını (saf konfigürasyon, projeler ve transkriptler) paketleyerek temiz bir aktarım sağlar.

---

## ✨ Öne Çıkan Özellikler

- **📁 Gruplanmış ve Net Arayüz:**
  - **Projeler:** Scratch klasöründeki projeler, bağımsız scriptler ve kullanıcının eklediği özel proje klasörleri ayrı bir grup olarak listelenir.
  - **Sistem & Yapılandırma:** Eklentiler (plugins), özel beceriler (skills), kurallar (rules), MCP yapılandırmaları ve konuşma geçmişi (brain) ayrı bir grupta toplanır.
  - Her grup için bağımsız *"Tümünü Seç / Kaldır"* kontrolü.
- **🔍 Akıllı Yedek Algılama (Auto-Detection):**
  - Program yedek klasörünün içerisine kendisinin de bir kopyasını koyar.
  - Klasörü yeni bilgisayara taşıyıp içindeki programı çalıştırdığınızda yanı başındaki yedekleri otomatik algılar ve *"Yedekleri bu bilgisayara aktarayım mı?"* diye sorar.
- **🛡️ Güvenli Filtreleme:** Sürüm çakışması yapabilecek `bin`, `builtin`, `lib`, `cache`, `daemon`, `installation_id` gibi sistem dosyaları otomatik olarak elenir.
- **📋 Standart Sağ Tık Menüsü:** Tüm yol ve giriş alanlarında *Kes, Kopyala, Yapıştır, Tümünü Seç* sağ tuş menüsü etkindir.
- **⚡ Tek Bağımsız EXE:** Harici hiçbir kütüphane veya Python kurulumu gerektirmeden tek dosya olarak çalışır.

---

## 🖥️ Kullanım Kılavuzu

### 1. Adım: Eski Bilgisayardan Dışa Aktarma (Yedekleme)
1. `AntigravityMigrator.exe` dosyasını çalıştırın.
2. **Dışa Aktar (Yedekle)** sekmesinde kaynak `.gemini` ve hedef yedek klasörünü kontrol edin.
3. **"🔍 Projeleri ve Verileri Tara"** butonuna basın.
4. Listelenen projelerden ve sistem verilerinden yedeklemek istediklerinizi onay kutularıyla (checkbox) seçin.
5. **"🚀 Yedeklemeyi Başlat"** butonuna tıklayın.
6. İşlem tamamlandığında hedef klasörde temiz bir yedek paketi, `backup_manifest.json` ve programın kendisi (`AntigravityMigrator.exe`) yer alacaktır.

### 2. Adım: Yeni Bilgisayara İçe Aktarma (Geri Yükleme)
1. Oluşturulan yedek klasörünü USB bellek veya ağ üzerinden yeni bilgisayarınıza aktarın.
2. Yedek klasörünün içindeki **`AntigravityMigrator.exe`** uygulamasını çalıştırın.
3. Karşınıza gelen *"Yedekler tespit edildi, bu bilgisayara aktarılsın mı?"* sorusuna **Evet** deyin.
4. İçe aktarmak istediğiniz öğeleri kontrol edip **"📥 İçe Aktarmayı Başlat"** butonuna basın.
5. Dosyalar yeni bilgisayarın Antigravity dizinlerine yerleştirilecektir.

---

## 🛠️ Geliştiriciler İçin

### Kaynak Koddan Çalıştırma

Projeyi kaynak koddan doğrudan Python ile çalıştırmak için:

```bash
# Bağımlılıkları yükleyin (yalnızca pyinstaller gerekir, arayüz tkinter ile çalışır)
pip install -r requirements.txt

# Programı başlatın
python migrator.py
```

### Tek Dosyalık EXE Olarak Derleme

Projeyi tek bir `.exe` haline getirmek için ister `build.bat` dosyasını çalıştırabilir, isterseniz terminalden şu komutu verebilirsiniz:

```bash
pyinstaller --onefile --noconsole --name "AntigravityMigrator" migrator.py
```

---

## 📂 Proje Yapısı

```
antigravity-migrator/
├── migrator.py               # Ana GUI ve yedekleme/geri yükleme motoru
├── AntigravityMigrator.exe    # Hazır derlenmiş tek dosya çalıştırılabilir sürüm
├── build.bat                 # Tek tıkla EXE derleme betiği
├── requirements.txt          # Geliştirici bağımlılıkları
├── .gitignore                # Git dışlama kuralları
├── LICENSE                   # MIT Lisansı
└── README.md                 # Detaylı dokümantasyon
```

---

## 📄 Lisans

Bu proje [MIT Lisansı](LICENSE) kapsamında sunulmaktadır.
