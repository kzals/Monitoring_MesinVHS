import threading
import time
import os
import sys
from collections import deque
from pathlib import Path
from dotenv import load_dotenv, set_key


import customtkinter as ctk
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusIOException
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

import pystray
from PIL import Image, ImageDraw
import serial.tools.list_ports

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("green")

BASE_DIR = Path(__file__).parent
ENV_PATH = BASE_DIR / ".env"
LOG_PATH = BASE_DIR / "vhs_gui.log"
ICO_PATH = BASE_DIR / "icon.ico"

load_dotenv(ENV_PATH)

NUM_CHANNELS = 3
REG_READINGS = [100, 101, 102]


def write_log(msg):
    with open(LOG_PATH, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {msg}\n")


def load_config():
    load_dotenv(ENV_PATH)
    return {
        "port": os.getenv("SERIAL_PORT", "COM9"),
        "slaves": os.getenv("SLAVES", "1,11"),
        "baudrate": int(os.getenv("BAUDRATE", "19200")),
        "interval": float(os.getenv("INTERVAL", "2.0")),
        "influx_url": os.getenv("INFLUX_URL", "http://localhost:8086"),
        "influx_token": os.getenv("INFLUX_TOKEN", ""),
        "influx_org": os.getenv("INFLUX_ORG", ""),
        "influx_bucket": os.getenv("INFLUX_BUCKET", ""),
        "influx_measurement": os.getenv("INFLUX_MEASUREMENT", "vhs"),
    "use_dummy": os.getenv("USE_DUMMY", "").lower() == "true",
    }


def save_config(cfg):
    for k, v in cfg.items():
        set_key(str(ENV_PATH), k.upper(), str(v))


def read_raw(client, slave, channel):
    try:
        resp = client.read_holding_registers(REG_READINGS[channel - 1], count=1, device_id=slave)
    except ModbusIOException:
        return None
    if resp is None or (hasattr(resp, "isError") and resp.isError()):
        return None
    raw = resp.registers[0]
    if raw == 0xFFFF or raw == 0x8000:
        return None
    return raw


def make_tray_icon(color=(0, 180, 0), width=64, height=64):
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = width // 8
    draw.ellipse(
        (margin, margin, width - margin, height - margin),
        fill=color + (255,),
    )
    return img


class ModbusThread(threading.Thread):
    def __init__(self, config, data_callback, status_callback):
        super().__init__(daemon=True)
        self.config = config
        self.data_callback = data_callback
        self.status_callback = status_callback
        self.stop_event = threading.Event()
        self._needs_restart = False

    def stop(self):
        self.stop_event.set()

    def restart(self, new_config):
        self.config = new_config
        self._needs_restart = True
        self.stop_event.set()

    def run(self):
        while True:
            self._run_loop()
            if self.stop_event.is_set() and not self._needs_restart:
                break

    def _run_loop(self):
        cfg = self.config
        slaves = [int(s.strip()) for s in cfg["slaves"].split(",")]
        modbus = None
        influx = None
        write_api = None

        try:
            modbus = ModbusSerialClient(
                port=cfg["port"], baudrate=cfg["baudrate"],
                bytesize=8, parity="N", stopbits=1, timeout=2,
            )
            if not modbus.connect():
                self.status_callback("com", False, f"COM {cfg['port']} failed")
                write_log(f"COM {cfg['port']} failed")
                if self.stop_event.wait(timeout=5):
                    return
            else:
                self.status_callback("com", True, f"COM {cfg['port']} connected")
                self.status_callback("com_detail", True, cfg["port"])
                for slave in slaves:
                    for ch in range(1, NUM_CHANNELS + 1):
                        try:
                            modbus.write_register(103 + ch, 1, device_id=slave)
                        except Exception as e:
                            write_log(f"Slave {slave} ch{ch} unit write error: {e}")
                    write_log(f"Slave {slave}: units set to Celsius")

            influx = InfluxDBClient(url=cfg["influx_url"], token=cfg["influx_token"], org=cfg["influx_org"])
            write_api = influx.write_api(write_options=SYNCHRONOUS)
            self.status_callback("influx", True, "connected")
            self.status_callback("ready", True, "")
            write_log("Backend started")

            while not self.stop_event.is_set():
                panel_data = {}
                all_ok = True

                for slave in slaves:
                    readings = {}
                    for ch in range(1, NUM_CHANNELS + 1):
                        val = read_raw(modbus, slave, ch)
                        readings[ch] = val
                        if val is None:
                            all_ok = False
                            write_log(f"Slave {slave} ch{ch}: read failed")
                    panel_data[slave] = readings

                timestamp = time.strftime("%H:%M:%S")
                self.data_callback(panel_data, timestamp, slaves)

                if all_ok and write_api:
                    try:
                        for slave in slaves:
                            idx = slaves.index(slave)
                            point = Point(cfg["influx_measurement"]).tag("panel", f"panel{idx + 1}")
                            for ch in range(1, NUM_CHANNELS + 1):
                                val = panel_data[slave].get(ch)
                                if val is not None:
                                    mc_num = (idx * NUM_CHANNELS) + ch
                                    point.field(f"panel{idx + 1}_mc{mc_num}", float(val))
                            write_api.write(bucket=cfg["influx_bucket"], org=cfg["influx_org"], record=point)
                    except Exception as e:
                        self.status_callback("influx", False, "write error")
                        write_log(f"InfluxDB error: {e}")

                if self.stop_event.wait(timeout=cfg["interval"]):
                    break

        except Exception as e:
            self.status_callback("ready", False, str(e))
            write_log(f"Backend error: {e}")
        finally:
            try:
                if modbus:
                    modbus.close()
            except:
                pass
            try:
                if influx:
                    influx.close()
            except:
                pass
            if self._needs_restart:
                self._needs_restart = False
                self.stop_event.clear()


class DummyThread(threading.Thread):
    def __init__(self, data_callback, status_callback):
        super().__init__(daemon=True)
        self.data_callback = data_callback
        self.status_callback = status_callback
        self.stop_event = threading.Event()
        self.t0 = time.time()

    def stop(self):
        self.stop_event.set()

    def _profile(self, elapsed):
        import math, random
        profiles = [
            {"func": lambda t: 6000 + 3000 * math.sin(t * 2 * math.pi / 480), "noise": 50},
            {"func": lambda t: 3500 + 100 * math.sin(t * 0.01), "noise": 20},
            {"func": lambda t: None, "noise": 0},
            {"func": lambda t: 2500 + max(0, (t - 120) / 360 * 9500) + 500 * math.sin(t * 0.02), "noise": 40},
            {"func": lambda t: 4000 + 1000 * math.sin(t * 2 * math.pi / 300), "noise": 30},
            {"func": lambda t: None if t < 60 else 4200 + 3800 * math.sin(t * 2 * math.pi / 400), "noise": 50},
        ]
        slaves_order = [1, 1, 1, 11, 11, 11]
        panel_data = {}
        for i, p in enumerate(profiles):
            slave = slaves_order[i]
            ch = (i % 3) + 1
            base = p["func"](elapsed)
            if base is None:
                val = None
            else:
                val = int(base + random.uniform(-p["noise"], p["noise"]))
            if slave not in panel_data:
                panel_data[slave] = {}
            panel_data[slave][ch] = val
        return panel_data

    def run(self):
        self.status_callback("com", True, "dummy mode")
        self.status_callback("influx", True, "dummy mode")
        self.status_callback("ready", True, "")
        while not self.stop_event.is_set():
            panel_data = self._profile(time.time() - self.t0)
            self.data_callback(panel_data, time.strftime("%H:%M:%S"), [1, 11])
            self.stop_event.wait(2)


class App:
    def __init__(self):
        self.config = load_config()
        self.backend = None
        self.token_visible = False
        self.tray = None
        self.buffers = {}

        self.root = ctk.CTk()
        self.root.title("Monitoring VHS")
        self.root.geometry("800x580")
        self.root.minsize(700, 480)
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

        if ICO_PATH.exists():
            try:
                self.root.iconbitmap(str(ICO_PATH))
            except:
                pass

        self.build_ui()
        self.build_tray()
        self.start_backend()
        self.root.mainloop()

    def build_ui(self):
        tab_bar = ctk.CTkFrame(self.root, fg_color="transparent")
        tab_bar.pack(fill="x", padx=8, pady=(8, 0))

        self.tab_btn = ctk.CTkSegmentedButton(
            tab_bar, values=["Monitor", "Settings"],
            font=("", 13),
            command=self.switch_tab,
        )
        self.tab_btn.pack(side="left")
        self.tab_btn.set("Monitor")

        self.content = ctk.CTkFrame(self.root, fg_color="transparent")
        self.content.pack(fill="both", expand=True, padx=8, pady=8)

        self.monitor_view = ctk.CTkFrame(self.content, fg_color="transparent")
        self.settings_view = ctk.CTkFrame(self.content, fg_color="transparent")
        self.build_monitor_view()
        self.build_settings_view()
        self.switch_tab("Monitor")

    def switch_tab(self, value):
        self.monitor_view.pack_forget()
        self.settings_view.pack_forget()
        if value == "Monitor":
            self.monitor_view.pack(fill="both", expand=True)
        elif value == "Settings":
            self.settings_view.pack(fill="both", expand=True)

    def build_monitor_view(self):
        status_box = ctk.CTkFrame(self.monitor_view, corner_radius=6, border_width=1, fg_color="white")
        status_box.pack(fill="both", expand=True, padx=8, pady=(10, 8))

        status_box.grid_columnconfigure(0, weight=1)
        status_box.grid_columnconfigure(1, weight=1)
        status_box.grid_rowconfigure(1, weight=0)
        status_box.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(status_box, text="Status", font=("", 13, "bold"), fg_color="white").grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(6, 2)
        )

        com_box = ctk.CTkFrame(status_box, corner_radius=6, border_width=1)
        com_box.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(0, 4))
        ctk.CTkLabel(com_box, text="COM Port", font=("", 13, "bold")).pack(pady=(6, 2))
        self.com_lbl = ctk.CTkLabel(com_box, text="\u25cf disconnected", font=("", 13, "bold"), text_color="gray")
        self.com_lbl.pack(pady=(0, 6))

        influx_box = ctk.CTkFrame(status_box, corner_radius=6, border_width=1)
        influx_box.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(0, 4))
        ctk.CTkLabel(influx_box, text="Server IT", font=("", 13, "bold")).pack(pady=(6, 2))
        self.influx_lbl = ctk.CTkLabel(influx_box, text="\u25cf disconnected", font=("", 13, "bold"), text_color="gray")
        self.influx_lbl.pack(pady=(0, 6))

        slaves = [int(s.strip()) for s in self.config["slaves"].split(",")]
        self.mc_temp_vars = {}
        self.mc_status_lbls = {}
        panel_titles = ["Panel 1", "Panel 2"]
        panel_mcs = [
            [("Mesin VHS 1", 1), ("Mesin VHS 2", 2), ("Mesin VHS 3", 3)],
            [("Mesin VHS 4", 1), ("Mesin VHS 5", 2), ("Mesin VHS 6", 3)],
        ]

        for col in range(2):
            frame = ctk.CTkFrame(status_box, corner_radius=6, border_width=1)
            frame.grid(row=2, column=col, sticky="nsew", padx=(12, 6) if col == 0 else (6, 12), pady=(4, 8))

            ctk.CTkLabel(frame, text=panel_titles[col], font=("", 13, "bold")).pack(pady=(8, 6))

            if col < len(slaves):
                slave = slaves[col]
                for mc_name, ch in panel_mcs[col]:
                    row = ctk.CTkFrame(frame, fg_color="transparent")
                    row.pack(fill="x", padx=18, pady=2)
                    row.grid_columnconfigure(0, weight=0)
                    row.grid_columnconfigure(1, weight=1)
                    row.grid_columnconfigure(2, weight=0, minsize=140)
                    row.grid_columnconfigure(3, weight=0, minsize=95)

                    ctk.CTkLabel(row, text=mc_name, font=("", 13), anchor="w").grid(row=0, column=0, sticky="w")
                    ctk.CTkLabel(row, text="", font=("", 13)).grid(row=0, column=1, sticky="ew")

                    var_temp = ctk.StringVar(value="---\u00b0C")
                    self.mc_temp_vars[(slave, ch)] = var_temp
                    ctk.CTkLabel(row, textvariable=var_temp, font=("Consolas", 24, "bold")).grid(row=0, column=2, sticky="e")

                    lbl_status = ctk.CTkLabel(row, text="\u25cf OFF", font=("", 12, "bold"), text_color="gray")
                    lbl_status.grid(row=0, column=3, sticky="w")
                    self.mc_status_lbls[(slave, ch)] = lbl_status

        footer = ctk.CTkFrame(self.monitor_view, fg_color="transparent")
        footer.pack(fill="x", padx=12, pady=(0, 10))
        self.time_lbl = ctk.CTkLabel(footer, text="", font=("", 12), text_color="gray")
        self.time_lbl.pack(side="left")

    def build_settings_view(self):
        main = ctk.CTkScrollableFrame(self.settings_view)
        main.pack(fill="both", expand=True, padx=4, pady=4)

        section_style = {"font": ("", 13, "bold"), "anchor": "w"}

        serial_box = ctk.CTkFrame(main, fg_color="white", corner_radius=6, border_width=1)
        serial_box.pack(fill="x", padx=8, pady=(8, 6))
        ctk.CTkLabel(serial_box, text="Serial Connection", **section_style).pack(padx=10, pady=(6, 4))
        sf = ctk.CTkFrame(serial_box, fg_color="transparent")
        sf.pack(fill="x", padx=10, pady=(0, 8))

        fields_serial = [
            ("COM Port:", "port", self.config["port"]),
            ("Slaves:", "slaves", self.config["slaves"]),
            ("Baudrate:", "baudrate", str(self.config["baudrate"])),
            ("Interval (s):", "interval", str(self.config["interval"])),
        ]
        self.entries = {}
        for label, key, val in fields_serial:
            row = ctk.CTkFrame(sf, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=label, width=100, anchor="w").pack(side="left")
            if key == "port":
                self.port_var = ctk.StringVar(value=val)
                self.entries["port"] = self.port_var
                combo = ctk.CTkFrame(row, fg_color="transparent")
                combo.pack(side="right")
                self.port_dropdown = ctk.CTkOptionMenu(
                    combo, variable=self.port_var, values=[val], width=356,
                )
                self.port_dropdown.pack(side="left", padx=(0, 4))
                self.scan_btn_set = ctk.CTkButton(
                    combo, text="Scan", font=("", 11),
                    command=self.scan_com_ports, width=60, height=28,
                )
                self.scan_btn_set.pack(side="left")
            else:
                var = ctk.StringVar(value=val)
                self.entries[key] = var
                ctk.CTkEntry(row, textvariable=var, width=420).pack(side="right")

        influx_box = ctk.CTkFrame(main, fg_color="white", corner_radius=6, border_width=1)
        influx_box.pack(fill="x", padx=8, pady=6)
        ctk.CTkLabel(influx_box, text="InfluxDB", **section_style).pack(padx=10, pady=(6, 4))
        inf = ctk.CTkFrame(influx_box, fg_color="transparent")
        inf.pack(fill="x", padx=10, pady=(0, 8))

        fields_influx = [
            ("URL:", "influx_url", self.config["influx_url"]),
            ("Token:", "influx_token", self.config["influx_token"]),
            ("Org:", "influx_org", self.config["influx_org"]),
            ("Bucket:", "influx_bucket", self.config["influx_bucket"]),
            ("Measurement:", "influx_measurement", self.config["influx_measurement"]),
        ]
        for label, key, val in fields_influx:
            row = ctk.CTkFrame(inf, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=label, width=100, anchor="w").pack(side="left")

            if key == "influx_token":
                self.token_var = ctk.StringVar(value=val)
                self.entries[key] = self.token_var
                btn = ctk.CTkButton(row, text="\U0001F441", width=30, command=self.toggle_token)
                btn.pack(side="right", padx=(0, 0))
                self.token_entry = ctk.CTkEntry(
                    row, textvariable=self.token_var, width=386, show="*",
                )
                self.token_entry.pack(side="right", padx=(0, 4))
            else:
                var = ctk.StringVar(value=val)
                self.entries[key] = var
                ctk.CTkEntry(row, textvariable=var, width=420).pack(side="right")

        auto_box = ctk.CTkFrame(main, corner_radius=6, border_width=1)
        auto_box.pack(fill="x", padx=8, pady=6)
        ctk.CTkLabel(auto_box, text="Auto-Start", **section_style).pack(padx=10, pady=(6, 4))
        af = ctk.CTkFrame(auto_box, fg_color="transparent")
        af.pack(fill="x", padx=10, pady=(0, 8))
        self.autostart_var = ctk.BooleanVar(value=self.is_autostart_enabled())
        ctk.CTkCheckBox(af, text="Start automatically with Windows", variable=self.autostart_var).pack(anchor="w")
        ctk.CTkLabel(
            af,
            text="The program will start minimized to the system tray when you log in.",
            font=("", 11),
            text_color="gray",
            justify="left",
        ).pack(anchor="w", padx=(22, 0), pady=(0, 4))

        dummy_box = ctk.CTkFrame(main, corner_radius=6, border_width=1)
        dummy_box.pack(fill="x", padx=8, pady=6)
        ctk.CTkLabel(dummy_box, text="Dummy Data", **section_style).pack(padx=10, pady=(6, 4))
        df = ctk.CTkFrame(dummy_box, fg_color="transparent")
        df.pack(fill="x", padx=10, pady=(0, 8))
        self.dummy_var = ctk.BooleanVar(value=self.config.get("use_dummy", False))
        ctk.CTkCheckBox(
            df, text="Gunakan data dummy (tanpa Modbus, tanpa InfluxDB)",
            variable=self.dummy_var,
            command=self.toggle_dummy,
        ).pack(anchor="w")
        ctk.CTkLabel(
            df,
            text="Aktifkan untuk simulasi: data virtual 6 mesin tanpa koneksi hardware.",
            font=("", 11),
            text_color="gray",
            justify="left",
        ).pack(anchor="w", padx=(22, 0), pady=(0, 4))

        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkButton(btn_frame, text="Save & Restart", command=self.save_settings).pack(side="right")

        self.settings_status = ctk.CTkLabel(main, text="", font=("", 12))
        self.settings_status.pack(anchor="w", padx=14, pady=(2, 6))



    def toggle_token(self):
        self.token_visible = not self.token_visible
        self.token_entry.configure(show="" if self.token_visible else "*")

    def is_autostart_enabled(self):
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_READ,
            )
            val, _ = winreg.QueryValueEx(key, "MonitoringVHS")
            winreg.CloseKey(key)
            return bool(val)
        except:
            return False

    def set_autostart(self, enable):
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE,
            )
            if enable:
                exe = sys.executable
                script = str(BASE_DIR / "vhs_gui.py")
                winreg.SetValueEx(key, "MonitoringVHS", 0, winreg.REG_SZ, f'"{exe}" "{script}"')
            else:
                try:
                    winreg.DeleteValue(key, "MonitoringVHS")
                except:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            self.settings_status.configure(text=f"Auto-Start error: {e}", text_color="red")

    def save_settings(self):
        try:
            cfg = {
                "port": self.entries["port"].get().strip(),
                "slaves": self.entries["slaves"].get().strip(),
                "baudrate": int(self.entries["baudrate"].get().strip()),
                "interval": float(self.entries["interval"].get().strip()),
                "influx_url": self.entries["influx_url"].get().strip(),
                "influx_token": self.entries["influx_token"].get().strip(),
                "influx_org": self.entries["influx_org"].get().strip(),
                "influx_bucket": self.entries["influx_bucket"].get().strip(),
                "influx_measurement": self.entries["influx_measurement"].get().strip(),
            }
            save_config(cfg)
            self.set_autostart(self.autostart_var.get())
            cfg["use_dummy"] = self.dummy_var.get()
            self.config = cfg
            self.start_backend()
            self.settings_status.configure(text="Settings saved. Backend restarted.", text_color="green")
            self.com_lbl.configure(text="\u25cf restarting...", text_color="orange")
        except ValueError as e:
            self.settings_status.configure(text=f"Invalid value: {e}", text_color="red")

    def build_tray(self):
        img = make_tray_icon((0, 180, 0))
        menu = pystray.Menu(
            pystray.MenuItem("Show", lambda: self.show_window()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self.exit_app),
        )
        self.tray = pystray.Icon("MonitoringVHS", img, "Monitoring VHS", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def update_tray_color(self, color):
        if self.tray:
            self.tray.icon = make_tray_icon(color)
            self.tray.update_menu()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()

    def hide_window(self):
        self.root.withdraw()

    def exit_app(self, *_):
        if self.backend:
            self.backend.stop()
        t = self.tray
        if t:
            self.tray = None
            t.visible = False
            t.stop()
        self.root.after(100, self.root.destroy)

    def scan_com_ports(self):
        self.scan_btn_set.configure(state="disabled", text="Scan..")

        try:
            ports = list(serial.tools.list_ports.comports())
        except Exception:
            self.scan_btn_set.configure(state="normal", text="Scan")
            return

        self.scan_btn_set.configure(state="normal", text="Scan")

        if not ports:
            self.settings_status.configure(text="No COM ports found", text_color="#cc0000")
            self.root.after(3000, lambda: self.settings_status.configure(text=""))
            return

        values = [p.device for p in ports]
        cur = self.port_var.get()
        self.port_dropdown.configure(values=values)
        if cur in values:
            self.port_var.set(cur)
        else:
            self.port_var.set(values[0])

    def toggle_dummy(self):
        use = self.dummy_var.get()
        self.config["use_dummy"] = use
        save_config(self.config)
        self.start_backend(dummy=use)

    def start_backend(self, dummy=None):
        if dummy is None:
            dummy = self.config.get("use_dummy", False)
        if self.backend:
            self.backend.stop()
        if dummy:
            self.backend = DummyThread(self.on_data, self.on_status)
        else:
            self.backend = ModbusThread(self.config, self.on_data, self.on_status)
        self.backend.start()

    def compute_status(self, key, temp):
        now = time.time()
        if temp is None:
            self.buffers.pop(key, None)
            return "OFF", "gray"
        if key not in self.buffers:
            self.buffers[key] = deque()
        buf = self.buffers[key]
        buf.append((now, temp))
        while buf and now - buf[0][0] > 300:
            buf.popleft()
        if len(buf) < 3:
            return "IDLE", "#1a7aff"
        max_temp = max(t for _, t in buf)
        cutoff = now - 60
        recent = [(ts, t) for ts, t in buf if ts >= cutoff]
        if len(recent) >= 2:
            first_ts, first_t = recent[0]
            last_ts, last_t = recent[-1]
            dt = last_ts - first_ts
            dT_per_min = ((last_t - first_t) / dt) * 60 if dt > 0 else 0
        else:
            dT_per_min = 0
        if dT_per_min > 2.0 or temp > 40:
            return "ACTIVE", "#1a7a1a"
        elif max_temp <= 42:
            return "IDLE", "#1a7aff"
        return "IDLE", "#1a7aff"

    def on_status(self, key, ok, msg):
        def update():
            if key == "com":
                self.com_lbl.configure(
                    text=f"\u25cf {msg}",
                    text_color=("#1a7a1a" if ok else "#cc0000"),
                )
            elif key == "influx":
                self.influx_lbl.configure(
                    text=f"\u25cf {msg}",
                    text_color=("#1a7a1a" if ok else "#cc0000"),
                )
            elif key == "ready":
                if ok:
                    self.update_tray_color((0, 180, 0))
                else:
                    self.update_tray_color((180, 0, 0))
                    self.start_backend()
        self.root.after(0, update)

    def on_data(self, panel_data, timestamp, slaves):
        def update():
            for slave in slaves:
                readings = panel_data.get(slave, {})
                for ch in range(1, NUM_CHANNELS + 1):
                    raw = readings.get(ch)
                    temp = raw / 100.0 if raw is not None else None
                    status_text, status_color = self.compute_status((slave, ch), temp)
                    tvar = self.mc_temp_vars.get((slave, ch))
                    if tvar:
                        if temp is not None:
                            tvar.set(f"{temp:.1f}\u00b0C")
                        else:
                            tvar.set("---\u00b0C")
                    slbl = self.mc_status_lbls.get((slave, ch))
                    if slbl:
                        slbl.configure(text=f"\u25cf {status_text}", text_color=status_color)
            self.time_lbl.configure(text=f"Last update: {timestamp}")
        self.root.after(0, update)


def main():
    App()


if __name__ == "__main__":
    main()
