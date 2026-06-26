import time
import math
import random
from collections import deque


NUM_CHANNELS = 3
SLAVES = [1, 11]


def compute_status(key, temp, buffers):
    now = time.time()
    if temp is None:
        buffers.pop(key, None)
        return "OFF", "gray"
    if key not in buffers:
        buffers[key] = deque()
    buf = buffers[key]
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


mc_profiles = [
    # mesin 1: naik-turun 8 menit
    {
        "name": "Mesin VHS 1", "slave": 1, "ch": 1,
        "func": lambda t: 6000 + 3000 * math.sin(t * 2 * math.pi / 480),
        "noise": 50,
    },
    # mesin 2: IDLE stabil
    {
        "name": "Mesin VHS 2", "slave": 1, "ch": 2,
        "func": lambda t: 3500 + 100 * math.sin(t * 0.01),
        "noise": 20,
    },
    # mesin 3: OFF (selalu None)
    {
        "name": "Mesin VHS 3", "slave": 1, "ch": 3,
        "func": lambda t: None,
        "noise": 0,
    },
    # mesin 4: dingin lalu panas (memanas setelah 120 detik)
    {
        "name": "Mesin VHS 4", "slave": 11, "ch": 1,
        "func": lambda t: 2500 + max(0, (t - 120) / 360 * 9500) + 500 * math.sin(t * 0.02),
        "noise": 40,
    },
    # mesin 5: bergelombang lambat
    {
        "name": "Mesin VHS 5", "slave": 11, "ch": 2,
        "func": lambda t: 4000 + 1000 * math.sin(t * 2 * math.pi / 300),
        "noise": 30,
    },
    # mesin 6: OFF 60 detik, lalu aktif
    {
        "name": "Mesin VHS 6", "slave": 11, "ch": 3,
        "func": lambda t: None if t < 60 else 4200 + 3800 * math.sin(t * 2 * math.pi / 400),
        "noise": 50,
    },
]


def generate_raw(profile, elapsed):
    base = profile["func"](elapsed)
    if base is None:
        return None
    noise = random.uniform(-profile["noise"], profile["noise"])
    return max(0, int(base + noise))


def main():
    print("=" * 70)
    print("  DUMMY S3600 — Simulasi Modbus 2 slave x 3 channel")
    print("  Raw = nilai register mentah  |  Display = raw / 100")
    print("=" * 70)
    print()

    buffers = {}
    t0 = time.time()

    try:
        while True:
            elapsed = time.time() - t0
            timestamp = time.strftime("%H:%M:%S")

            print(f"\n--- {timestamp} (t={elapsed:.0f}s) ---", flush=True)

            for mc in mc_profiles:
                raw = generate_raw(mc, elapsed)
                temp = raw / 100.0 if raw is not None else None
                key = (mc["slave"], mc["ch"])
                status, color = compute_status(key, temp, buffers)

                raw_str = f"raw={raw:>5d}" if raw is not None else "raw=  OFF"
                temp_str = f"{temp:.1f}C" if temp is not None else "  OFF "
                lbl = f"S{mc['slave']:>2} CH{mc['ch']} | {raw_str} | {temp_str:>7s} | {status}"
                print(f"  {mc['name']:<12s} {lbl}", flush=True)

            time.sleep(2)

    except KeyboardInterrupt:
        print("\nDummy stopped.")


if __name__ == "__main__":
    main()
