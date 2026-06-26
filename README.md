# Monitoring VHS

Aplikasi monitoring suhu mesin VHS secara *real-time* melalui komunikasi **Modbus Serial** (RS-485) ke panel S3600, dengan penyimpanan data ke **InfluxDB**.

Mendukung **2 panel × 3 channel** (total 6 mesin) dengan dua modus: pembacaan hardware via Modbus atau simulasi data dummy.

## Fitur

- **Monitoring real-time** — tampilan suhu 6 mesin dalam °C, diperbarui setiap interval tertentu
- **Status mesin** — deteksi OFF / IDLE / ACTIVE berdasarkan tren suhu 5 menit
- **Dummy mode** — simulasi data virtual tanpa hardware (berguna untuk demo)
- **Konfigurasi melalui GUI** — atur COM port, slave address, InfluxDB, dsb.
- **Auto-start Windows** — jalan otomatis saat login (minimize ke system tray)
- **CLI mode** — `vhs.py` untuk headless/scripting tanpa GUI
- **Data logging** — kirim data suhu ke InfluxDB untuk analisis lanjutan

## Persyaratan

- Python 3.14+
- Windows (menggunakan pywin32 untuk auto-start, COM port)

## Instalasi

```bash
# 1. Clone repositori
git clone https://github.com/your-username/MonitoringVHS.git
cd MonitoringVHS

# 2. Buat virtual environment (opsional tapi disarankan)
python -m venv venv
venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Siapkan konfigurasi
copy .env.example .env
```

Edit `.env` dan isi kredensial InfluxDB serta konfigurasi serial port sesuai lingkungan Anda.

## Penggunaan

### GUI (direkomendasikan)

```bash
python vhs_gui.py
```

Aplikasi akan muncul dengan dua tab:
- **Monitor** — tampilan suhu dan status mesin secara *real-time*
- **Settings** — konfigurasi serial port, InfluxDB, dan opsi lainnya

### CLI (headless / scripting)

```bash
python vhs.py --port COM9 --slaves 1,11 --baud 19200 --interval 2.0
```

Argumen CLI:

| Argumen | Default | Deskripsi |
|---------|---------|-----------|
| `--port` | dari `.env` / COM3 | Port serial |
| `--slaves` | dari `.env` / 1,11 | Daftar slave address (dipisah koma) |
| `--baud` | 19200 | Baudrate |
| `--interval` | 2.0 | Interval baca (detik) |
| `--count` | 0 | Jumlah pembacaan (0 = terus-menerus) |
| `--influx-url` | dari `.env` | URL InfluxDB |
| `--influx-token` | dari `.env` | Token InfluxDB |
| `--influx-org` | dari `.env` | Organisasi InfluxDB |
| `--influx-bucket` | dari `.env` | Bucket InfluxDB |
| `--influx-measurement` | dari `.env` | Measurement InfluxDB |

### Dummy / Simulasi

Tanpa koneksi hardware:

```bash
# Via GUI: centang "Gunakan data dummy" di tab Settings
python vhs_gui.py

# CLI dummy standalone (hanya tampilan terminal):
python dummy_s3600.py
```

## Struktur Proyek

| File | Deskripsi |
|------|-----------|
| `vhs_gui.py` | Aplikasi utama GUI (CustomTkinter) |
| `vhs.py` | CLI version — headless Modbus reader |
| `dummy_s3600.py` | Simulasi data dummy di terminal |
| `.env` | Konfigurasi lokal (tidak ikut push) |
| `.env.example` | Template konfigurasi (push ke repo) |
| `requirements.txt` | Dependencies Python |
| `.gitignore` | File/folder yang tidak ikut version control |
| `MonitoringVHS.spec` | PyInstaller build spec |
| `MonitoringVHS_Setup.iss` | Inno Setup installer script |
| `icon.ico` | Icon aplikasi |
| `img/` | Asset gambar untuk web |

## Konfigurasi `.env`

| Variable | Contoh | Deskripsi |
|----------|--------|-----------|
| `SERIAL_PORT` | `COM9` | Port serial untuk Modbus |
| `SLAVES` | `1,11` | Daftar slave address (pisah koma) |
| `BAUDRATE` | `19200` | Baudrate komunikasi serial |
| `INTERVAL` | `2.0` | Interval pembacaan dalam detik |
| `INFLUX_URL` | `http://localhost:8086` | URL server InfluxDB |
| `INFLUX_TOKEN` | `your_token` | Token autentikasi InfluxDB |
| `INFLUX_ORG` | `my-org` | Nama organisasi InfluxDB |
| `INFLUX_BUCKET` | `monitoring_vhs` | Nama bucket InfluxDB |
| `INFLUX_MEASUREMENT` | `vhs` | Nama measurement InfluxDB |
| `USE_DUMMY` | `True` / `False` | Aktifkan mode dummy |

## Build `.exe` (opsional)

```bash
pip install pyinstaller
pyinstaller MonitoringVHS.spec
```

Hasil build ada di folder `dist/`.

Untuk membuat installer:

1. Install [Inno Setup](https://jrsoftware.org/isdl.php)
2. Klik kanan `MonitoringVHS_Setup.iss` → **Compile**
