#!/usr/bin/env python3
"""
Fake vehicle gateway: pushes steering INTO the middleware (like the real CAN/Ethernet reader will)
and pretends that curbs are visible in the camera frames (POST /api/v1/ingest/vision, TEST input).
There are no distance sensors any more: the zones normally come from the camera frames by themselves.
Python 3.8+, standard library only.

  python tools/fake_vehicle.py                       # scripted parallel-parking, loops
  python tools/fake_vehicle.py --scenario sweep      # steering sweeps +-450 deg
  python tools/fake_vehicle.py --scenario static --steer 200 --rear 0.4 --left 0.7 --front 1.2
  python tools/fake_vehicle.py --host 192.168.1.20 --port 9090 --token SECRET

Start the server WITHOUT -Dwp5.simulate=true, otherwise two sources fight.
"""
import argparse, json, math, sys, time, urllib.request, urllib.error


def post(base, path, obj, token):
    req = urllib.request.Request(base + path, data=json.dumps(obj).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("X-Api-Key", token)
    with urllib.request.urlopen(req, timeout=2) as r:
        return r.status


def curb(side, d):
    """TEST curb 'seen in a camera frame'. side = left | right | front | rear. d = metres, None = no curb in that frame."""
    return {"side": side, "detected": d is not None, "distanceM": 1.0 if d is None else round(d, 3), "confidence": 1.0}


def smooth(t, a, b):          # 0..1 ramp between times a and b
    return min(1.0, max(0.0, (t - a) / (b - a)))


def parking(t):
    """~30 s loop: reverse, turn wheel right, then left; a curb on the left and one behind get closer."""
    t = t % 30.0
    if t < 4:    swa = 0
    elif t < 10: swa = -450 * smooth(t, 4, 10)             # full right lock
    elif t < 14: swa = -450
    elif t < 22: swa = -450 + 900 * smooth(t, 14, 22)      # swing to full left lock
    else:        swa = 450 * (1 - smooth(t, 22, 28))       # straighten
    rear = 2.2 - 1.9 * smooth(t, 3, 26)                    # 2.2 m -> 0.3 m   (curb / wheel stop behind the car)
    left = 1.6 - 1.1 * smooth(t, 8, 24)                    # 1.6 m -> 0.5 m   (curb along the left side)
    return swa, "R", 1.5, [curb("rear", rear), curb("left", left), curb("right", None), curb("front", None)]


def sweep(t):
    swa = 450 * math.sin(2 * math.pi * t / 12.0)
    return swa, "R", 1.5, [curb("rear", 1.35 + 1.15 * math.sin(2 * math.pi * t / 8.0)),
                           curb("left", 0.7 + 0.45 * math.sin(2 * math.pi * t / 6.0 + 1))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost"); ap.add_argument("--port", type=int, default=9091)
    ap.add_argument("--token", default=""); ap.add_argument("--rate", type=float, default=20.0, help="messages per second")
    ap.add_argument("--scenario", choices=["park", "sweep", "static"], default="park")
    ap.add_argument("--duration", type=float, default=0, help="seconds, 0 = forever")
    ap.add_argument("--steer", type=float, default=0); ap.add_argument("--gear", default="R")
    ap.add_argument("--left", type=float, default=None); ap.add_argument("--right", type=float, default=None)
    ap.add_argument("--front", type=float, default=None); ap.add_argument("--rear", type=float, default=None)
    a = ap.parse_args()
    base = "http://%s:%d" % (a.host, a.port)
    print("Sending to", base, "scenario:", a.scenario, " (Ctrl+C to stop)")
    t0, n = time.time(), 0
    try:
        while a.duration <= 0 or time.time() - t0 < a.duration:
            t = time.time() - t0
            if a.scenario == "park":    swa, gear, spd, s = parking(t)
            elif a.scenario == "sweep": swa, gear, spd, s = sweep(t)
            else:
                swa, gear, spd = a.steer, a.gear, 1.5
                s = [curb("left", a.left), curb("right", a.right), curb("front", a.front), curb("rear", a.rear)]
            post(base, "/api/v1/ingest/steering", {"steeringWheelDeg": round(swa, 2), "gear": gear, "speedKph": spd}, a.token)
            post(base, "/api/v1/ingest/vision", {"sides": s}, a.token)
            n += 1
            if n % int(max(1, a.rate)) == 0:
                print("t=%5.1fs  steering %7.1f deg  gear %s  %s" % (t, swa, gear,
                      "  ".join("%s=%s" % (x["side"], "no curb" if not x["detected"] else "%.2f m" % x["distanceM"]) for x in s)))
            time.sleep(1.0 / a.rate)
    except urllib.error.URLError as e:
        sys.exit("Cannot reach middleware at %s: %s\nIs the server running and the port right?" % (base, e))
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
