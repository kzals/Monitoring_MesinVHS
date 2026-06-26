import logging
import time
import argparse
import sys
import os
from dotenv import load_dotenv  # --- UNTUK BACA .ENV ---
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusIOException

# --- IMPORT INFLUXDB ---
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# Load konfigurasi dari file .env
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("S3600")

NUM_CHANNELS = 3
REG_READINGS = [100, 101, 102]


def read_raw(client, slave, channel):
    try:
        resp = client.read_holding_registers(REG_READINGS[channel - 1], count=1, device_id=slave)
    except ModbusIOException:
        return None
    
    # Proteksi jika resp kosong / error
    if resp is None or hasattr(resp, 'isError') and resp.isError():
        return None
        
    raw = resp.registers[0]
    if raw == 0xFFFF or raw == 0x8000:
        return None
    return raw


def main():
    parser = argparse.ArgumentParser(description="S3600 Multi-Panel Raw Reader")
    parser.add_argument("--port", default="COM3", help="Serial port (default: COM3)")
    parser.add_argument("--slaves", default="1,11", help="Daftar slave address, pisah dengan koma (default: 1,11)")
    parser.add_argument("--baud", type=int, default=19200, help="Baudrate (default: 19200)")
    parser.add_argument("--interval", type=float, default=2.0, help="Interval baca dalam detik (default: 2.0)")
    parser.add_argument("--count", type=int, default=0, help="Jumlah pembacaan, 0 = terus-menerus")
    
    # Argumen InfluxDB sekarang mengambil default dari file .env
    parser.add_argument("--influx-url", default=os.getenv("INFLUX_URL", "http://localhost:8086"), help="URL InfluxDB")
    parser.add_argument("--influx-token", default=os.getenv("INFLUX_TOKEN", ""), help="Token InfluxDB")
    parser.add_argument("--influx-org", default=os.getenv("INFLUX_ORG", "my-org"), help="Organisasi InfluxDB")
    parser.add_argument("--influx-bucket", default=os.getenv("INFLUX_BUCKET", "monitoring_vhs"), help="Bucket InfluxDB")
    parser.add_argument("--influx-measurement", default=os.getenv("INFLUX_MEASUREMENT", "vhs_measurement"), help="Measurement InfluxDB")
    
    args = parser.parse_args()

    slaves = [int(s.strip()) for s in args.slaves.split(",")]
    log.info("=" * 50)
    log.info("S3600 Multi-Panel Raw Reader started")
    log.info("Port: %s | Slaves: %s | Baud: %d", args.port, slaves, args.baud)

    # --- INFLUXDB INITIALIZATION ---
    log.info("Menghubungkan ke InfluxDB di %s...", args.influx_url)
    influx_client = InfluxDBClient(url=args.influx_url, token=args.influx_token, org=args.influx_org)
    write_api = influx_client.write_api(write_options=SYNCHRONOUS)

    # Modbus client setup
    client = ModbusSerialClient(
        port=args.port, baudrate=args.baud, bytesize=8, parity="N", stopbits=1, timeout=2
    )
    if not client.connect():
        log.error("Gagal terhubung ke %s", args.port)
        sys.exit(1)
    log.info("Terhubung ke %s", args.port)

    count = 0
    try:
        while True:
            for slave in slaves:
                parts = []
                slave_index = slaves.index(slave)  # 0 untuk slave pertama, 1 untuk slave kedua, dst.
                panel_num = slave_index + 1
                panel_name = f"panel{panel_num}"
                
                # Sesuai permintaan: Tag "slave_id" DIHAPUS, hanya menyisakan tag "panel"
                point = Point(args.influx_measurement).tag("panel", panel_name)
                has_data = False

                for ch in range(1, NUM_CHANNELS + 1):
                    val = read_raw(client, slave, ch)
                    
                    # Rumus matematika untuk menentukan nomor mc secara berurutan:
                    # Slave 1 (indeks 0): ch 1->mc1, ch 2->mc2, ch 3->mc3
                    # Slave 2 (indeks 1): ch 1->mc4, ch 2->mc5, ch 3->mc6
                    mc_num = (slave_index * NUM_CHANNELS) + ch
                    field_name = f"{panel_name}_mc{mc_num}"

                    if val is not None:
                        parts.append(f"CH{ch}: {val}")
                        # Simpan ke InfluxDB dengan nama field baru (contoh: panel1_mc1)
                        point.field(field_name, float(val))
                        has_data = True
                    else:
                        parts.append(f"CH{ch}: ERROR")
                
                log.info("Panel#%d (slave=%d): %s", panel_num, slave, " | ".join(parts))

                # Kirim ke InfluxDB
                if has_data:
                    try:
                        write_api.write(bucket=args.influx_bucket, org=args.influx_org, record=point)
                        log.info("-> Data %s berhasil dikirim ke InfluxDB", panel_name)
                    except Exception as e:
                        log.error("-> Gagal mengirim data ke InfluxDB: %s", e)

            count += 1
            if args.count > 0 and count >= args.count:
                log.info("Selesai: %d kali pembacaan", count)
                break

            time.sleep(args.interval)
    except KeyboardInterrupt:
        log.info("Dihentikan oleh user")
    finally:
        client.close()
        influx_client.close()
        log.info("Koneksi ditutup")


if __name__ == "__main__":
    main()