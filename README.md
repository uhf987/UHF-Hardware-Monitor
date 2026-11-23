# UHF Hardware Monitor

Windows için küçük, şeffaf sistem monitörü. Ekranın **sağ alt köşesinde, saatin hemen üstünde** durur; arka planı yoktur, yarı saydamdır ve **tıklanamaz** (fare tıklamaları altındaki pencereye geçer). NVIDIA'nın FPS göstergesi gibi masaüstünde görünür ama kalabalık yaratmaz.

```
CPU   ▰▰▱▱▱▱▱   12%  45°C
RAM   ▰▰▰▰▱▱▱   8.2/32 GB
GPU   ▰▱▱▱▱▱▱    5%  40°C
VRAM  ▰▰▱▱▱▱▱   2.1/12 GB
DİSK  ▰▰▰▱▱▱▱   42%
```

## Ne gösterir?

| Satır | İçerik | Kaynak |
|-------|--------|--------|
| CPU   | Kullanım % + sıcaklık | psutil + LibreHardwareMonitorLib |
| RAM   | Kullanılan / toplam GB | psutil |
| GPU   | Kullanım % + sıcaklık | pynvml (yalnızca NVIDIA) |
| VRAM  | Kullanılan / toplam GB | pynvml |
| DİSK  | `C:\` doluluk % | psutil |

Çubuk kullanım yüzdesini gösterir. Renk, kullanım ve sıcaklıktan **kötü olana** göre değişir:
yeşil (%60 altı), sarı (%60–85), kırmızı (%85 ve üstü). Örneğin CPU %10 yükte ama 90 °C ise çubuk kırmızı olur.

NVIDIA ekran kartı yoksa GPU ve VRAM satırları otomatik gizlenir.

## Kısayollar

Her pencerede çalışır, program odakta olmasa da geçerlidir.

| Kısayol | İşlev |
|---------|-------|
| **Ctrl+Alt+F10** | Düzenleme modunu aç/kapat |
| **Ctrl+Alt+F12** | Programı kapat |

**Düzenleme modu:** Gösterge arkasında koyu bir panelle belirir. Sürükleyerek taşıyabilir, sağ tıkla menüyü açabilirsin (Kilitle, Düzenlerken her zaman üstte tut, Konumu sıfırla, PawnIO kur, Çıkış). Tekrar Ctrl+Alt+F10 ile kilitlenir ve yine tıklanamaz olur. Taşıdığın konum kaydedilir.

**Üstte kalma:** Kilitliyken gösterge her zaman diğer pencerelerin üstündedir. Düzenleme modunda ise normal pencere gibi davranır, böylece sağ tık menüsü üstte kalır. Düzenlerken de göstergenin her zaman üstte durmasını istersen menüdeki "Düzenlerken her zaman üstte tut" seçeneğini işaretle (seçim kaydedilir).

## Gereksinimler

- Windows 10 / 11
- Python 3.11 veya 3.12 önerilir (çok yeni sürümlerde `pythonnet` uyumsuz olabilir)
- Paketler:
  ```
  pip install psutil nvidia-ml-py pythonnet
  ```
- CPU sıcaklığı için, program dosyasının yanında şu DLL'ler:
  - `LibreHardwareMonitorLib.dll`
  - `System.Buffers.dll`
  - `System.Memory.dll`
  - `System.Numerics.Vectors.dll`
  - `System.Runtime.CompilerServices.Unsafe.dll`
- CPU sıcaklığı için **yönetici izni** ve **PawnIO sürücüsü** (aşağıya bak)

## Çalıştırma

```
python UHF_Hardware_Monitor.py
```

Yönetici değilsen program UAC ile kendini yeniden başlatır (`AUTO_ELEVATE`). Yönetici izni olmadan da çalışır, ama CPU sıcaklığı görünmez.

### PawnIO sürücüsü

CPU sıcaklığı okumak için PawnIO gerekir. Kurulu değilse program ilk açılışta winget ile otomatik kurmayı teklif eder. Sonradan kurmak için düzenleme modunda sağ tık menüsünden "PawnIO sürücüsünü kur…" seçilebilir. Otomatik kurulum olmazsa <https://pawnio.eu> adresinden elle kurulur.

## EXE oluşturma

1. Gerekli paketleri kur:
   ```
   python -m pip install --upgrade pyinstaller psutil nvidia-ml-py pythonnet
   ```
2. `UHF_Hardware_Monitor.py` ve 5 DLL'yi aynı klasöre koy.
3. **Önce** bu klasörü Windows Defender dışlamalarına ekle (Windows Güvenliği → Virüs ve tehdit koruması → Ayarları yönet → Dışlamalar). Aksi halde antivirüs oluşan exe'yi silip boş bırakabilir.
4. CMD'yi aç ve klasöre geç (komutu yanlış klasörde çalıştırırsan DLL'ler bulunamaz):
   ```
   cd /d "C:\klasörün\yolu"
   dir
   ```
   `dir` çıktısında `UHF_Hardware_Monitor.py` ve DLL'ler görünmeli.
5. Derleme komutunu çalıştır:
   ```
   python -m PyInstaller --noconfirm --clean --onedir --windowed --name UHF_Hardware_Monitor --uac-admin --hidden-import pynvml --hidden-import clr --collect-all pythonnet --collect-all clr_loader --add-data "LibreHardwareMonitorLib.dll;." --add-data "System.Buffers.dll;." --add-data "System.Memory.dll;." --add-data "System.Numerics.Vectors.dll;." --add-data "System.Runtime.CompilerServices.Unsafe.dll;." UHF_Hardware_Monitor.py
   ```
6. Sonuç: `dist\UHF_Hardware_Monitor\UHF_Hardware_Monitor.exe`

Çıktı tek dosya değil bir klasördür; exe'yi tek başına taşıma, klasörün tamamını taşı. Exe her açılışta yönetici izni ister (`--uac-admin`). İstersen komuta `--icon icon.ico` ekleyerek ikon ekleyebilirsin.

Derleme sırasında program çalışıyorsa `dist` klasörü silinemez ve hata alırsın. Önce programı kapat (Ctrl+Alt+F12 veya `taskkill /f /im UHF_Hardware_Monitor.exe`).

## Ayarlar

Hepsi `UHF_Hardware_Monitor.py` dosyasının başındaki sabitlerle değiştirilir. Değişiklikten sonra exe'yi yeniden derlemen gerekir.

| Sabit | Açıklama |
|-------|----------|
| `OVERLAY_ALPHA` | Saydamlık: 0.0 (görünmez) – 1.0 (opak). Varsayılan 0.75 |
| `OVERLAY_PT` | Yazı boyutu |
| `BAR_W`, `BAR_H` | Renkli çubuğun genişlik ve yüksekliği |
| `OVERLAY_MARGIN_X` | Sağ kenardan boşluk; büyüttükçe sola kayar |
| `OVERLAY_MARGIN_Y` | Görev çubuğundan boşluk; büyüttükçe yukarı çıkar |
| `OVERLAY_ROWS` | Gösterilecek satırlar; istemediğini listeden sil |
| `OV_LEVELS` | Yeşil / sarı / kırmızı renkleri |
| `DISK_PATH` | İzlenecek disk (varsayılan `C:\`) |
| `REFRESH_MS` | Yenileme aralığı (ms) |
| `AUTO_ELEVATE` | Yönetici değilse UAC ile yeniden başlat |
| `HOTKEY_EDIT_VK`, `HOTKEY_QUIT_VK` | Kısayol tuşları (sanal tuş kodu) |

## Dosyalar

Programın kullanıcı klasöründe (`C:\Users\<kullanıcı>\`) oluşturduğu dosyalar:

| Dosya | Amaç |
|-------|------|
| `uhf_hardware_monitor.json` | Kaydedilen konum |
| `uhf_hardware_monitor_error.log` | CPU sıcaklığı okuyucu hataları |
| `uhf_hardware_monitor_debug.log` | Konum hesabı teşhis kaydı |

Konum bozulduysa düzenleme modunda "Konumu sıfırla" seçilir ya da `uhf_hardware_monitor.json` silinir.

## Windows açılışında otomatik başlatma

Program yönetici izni istediği için Başlangıç klasörü çalışmaz. Bunun yerine **Görev Zamanlayıcı**'da yeni bir görev oluştur:

1. Tetikleyici: "Oturum açıldığında"
2. Eylem: Programı başlat → `UHF_Hardware_Monitor.exe`
3. "En yüksek ayrıcalıklarla çalıştır" kutusunu işaretle

## Sorun giderme

| Sorun | Çözüm |
|-------|-------|
| CPU sıcaklığı görünmüyor | Yönetici olarak çalıştır, DLL'lerin yanında olduğunu ve PawnIO'nun kurulu olduğunu kontrol et. Ayrıntı için `uhf_hardware_monitor_error.log` dosyasına bak |
| GPU / VRAM satırları yok | Yalnızca NVIDIA kartlar desteklenir; sürücünün kurulu olduğundan emin ol |
| Oyunda görünmüyor | "Tam ekran (exclusive)" modunda Windows üstte pencere göstermez. Oyunu "pencereli tam ekran (borderless)" modunda çalıştır |
| Exe çok küçük (1 KB) / "uygulama çalışmıyor" uyarısı | Antivirüs exe'yi bozmuş olabilir. Windows Güvenliği → Koruma geçmişi'ne bak, klasörü dışlamalara ekleyip yeniden derle |
| Gösterge yanlış yerde açılıyor | Ctrl+Alt+F10 ile taşı veya "Konumu sıfırla" kullan. Olmazsa `uhf_hardware_monitor_debug.log` dosyasına bak |
| Kapatmak istiyorum | Ctrl+Alt+F12 |
