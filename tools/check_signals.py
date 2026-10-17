#!/usr/bin/env python3
"""
Android-side acceptance test, run from a PC (or any machine that can reach the middleware).
Connects to /api/v1/signals exactly like the app, then CHECKS what arrives.
Python 3.8+, standard library only.

  python tools/check_signals.py --host 192.168.1.20 --port 9091            # listen 8 s, validate the stream
  python tools/check_signals.py --port 9091 --roundtrip                    # also: POST a value, measure latency to SSE

--roundtrip drives the middleware itself, so start the server WITHOUT -Dwp5.simulate=true.
Exit code 0 = all checks passed, 1 = something failed.
"""
import argparse, json, sys, threading, time, urllib.request

LEVEL = {"NONE": 0, "SAFE": 1, "WARNING": 2, "DANGER": 3}
fails, passes = [], []


def check(ok, msg):
    (passes if ok else fails).append(msg)
    print(("  PASS  " if ok else "  FAIL  ") + msg)


def sse(url, on_event, stop):
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    with urllib.request.urlopen(req, timeout=15) as r:
        ev, data = None, []
        for raw in r:
            if stop.is_set():
                return
            line = raw.decode("utf-8").rstrip("\r\n")
            if line.startswith(":"):
                continue
            if line == "":
                if ev and data:
                    on_event(ev, json.loads("\n".join(data)), time.time())
                ev, data = None, []
            elif line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())


def validate_steering(m):
    need = ["seq", "ts", "valid", "steeringWheelDeg", "leftWheelDeg", "rightWheelDeg", "roadWheelAvgDeg",
            "direction", "gear", "guideLeft", "guideRight", "model"]
    miss = [k for k in need if k not in m]
    check(not miss, "steering has all required fields" + (" - missing %s" % miss if miss else ""))
    if miss:
        return
    sw, l, r = m["steeringWheelDeg"], m["leftWheelDeg"], m["rightWheelDeg"]
    if abs(sw) > 20:
        left_turn = sw > 0
        check((l > 0) == left_turn and (r > 0) == left_turn, "wheel angle sign follows steering sign (+ = LEFT)")
        check(abs(l) >= abs(r) if left_turn else abs(r) >= abs(l), "Ackermann: inner wheel turns more than outer")
        check(m["direction"] == ("LEFT" if left_turn else "RIGHT"), "direction text matches sign")
        check(abs(m["roadWheelAvgDeg"]) <= max(abs(l), abs(r)) + 0.01, "roadWheelAvgDeg between the two wheels")
    tr = {t["key"]: t for t in m["model"]["transforms"]}
    for key in ("steeringWheel", "wheelFrontLeft", "wheelFrontRight"):
        check(key in tr, "model.transforms contains binding '%s'" % key)
    if "steeringWheel" in tr:
        b = tr["steeringWheel"]
        check(abs(b["angleDeg"] - max(-540, min(540, sw))) < 0.05,
              "steeringWheel transform angle (%.1f) equals steeringWheelDeg (%.1f) (sign=1, offset=0 in profile)" % (b["angleDeg"], sw))
    for key, src in (("wheelFrontLeft", l), ("wheelFrontRight", r)):
        if key in tr:
            check(abs(tr[key]["angleDeg"] - max(-45, min(45, src))) < 0.05, "%s transform angle (%.1f) equals %s wheel angle (%.1f)" % (key, tr[key]["angleDeg"], key[10:], src))
    check(len(m["guideLeft"]) >= 2 and len(m["guideLeft"]) == len(m["guideRight"]), "guide lines have equal point counts (%d)" % len(m["guideLeft"]))
    if m["gear"] == "R":
        check(m["guideLeft"][-1][0] < 0, "gear R: guide lines run backwards (negative x)")


def validate_curb(m):
    W = m["panorama"]["width"]
    d_th, w_th = m["thresholds"]["dangerM"], m["thresholds"]["warningM"]
    bad = []
    for z in m["zones"]:
        d = z["distanceM"]
        if d is not None:
            if d <= d_th and z["zone"] != "DANGER": bad.append("%s %.2fm should be DANGER" % (z["pos"], d))
            if d > w_th + 0.06 and z["zone"] != "SAFE": bad.append("%s %.2fm should be SAFE" % (z["pos"], d))
        if z["pano"]["x"] < 0 or z["pano"]["x"] + z["pano"]["w"] > W: bad.append("%s outside panorama" % z["pos"])
        if abs(z["pano"]["xNorm"] * W - z["pano"]["x"]) > 1.5: bad.append("%s xNorm inconsistent" % z["pos"])
        if z["color"].upper() != {"DANGER": "#FF3B30", "WARNING": "#FFCC00", "SAFE": "#34C759", "NONE": "#9E9E9E"}[z["zone"]]:
            bad.append("%s colour %s not default for %s (ok if you customised colours)" % (z["pos"], z["color"], z["zone"]))
    check(not bad, "curb zones: thresholds, colours and pixel ranges consistent" + ("  " + "; ".join(bad) if bad else ""))
    worst = max([LEVEL[z["zone"]] for z in m["zones"]] or [0])
    check(LEVEL[m["summary"]["worstZone"]] == worst, "summary.worstZone equals worst zone in list")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost"); ap.add_argument("--port", type=int, default=9091)
    ap.add_argument("--seconds", type=float, default=8); ap.add_argument("--roundtrip", action="store_true")
    ap.add_argument("--token", default="")
    a = ap.parse_args()
    base = "http://%s:%d" % (a.host, a.port)
    print("== 1. REST endpoints at", base)
    try:
        prof = json.load(urllib.request.urlopen(base + "/api/v1/profile", timeout=4))
        check(True, "GET /api/v1/profile  ->  %s" % prof.get("profileName"))
        check(prof.get("profileName") not in (None, "default-generic"), "real vehicle profile loaded (not the placeholder)")
        b = prof.get("bindings", {})
        check(all(k in b for k in ("steeringWheel", "wheelFrontLeft", "wheelFrontRight")), "profile has steeringWheel + both front wheel bindings")
        meta = json.load(urllib.request.urlopen(base + "/meta", timeout=4))
        check(meta["panorama"]["width"] > 0 and len(meta["cameras"]) == 4, "GET /meta  ->  panorama %dx%d, %d cameras" % (meta["panorama"]["width"], meta["panorama"]["height"], len(meta["cameras"])))
    except Exception as e:
        print("  FAIL  cannot reach middleware:", e); sys.exit(1)

    print("\n== 2. Live stream /api/v1/signals for %.0f s" % a.seconds)
    got = {"steering": [], "curb": []}; stop = threading.Event(); lock = threading.Lock()
    firsts = {}

    def on_event(ev, msg, ts):
        with lock:
            if ev in got:
                got[ev].append((ts, msg))
                if ev not in firsts:
                    firsts[ev] = msg

    th = threading.Thread(target=lambda: sse(base + "/api/v1/signals", on_event, stop), daemon=True); th.start()

    rt = {}
    if a.roundtrip:
        time.sleep(1.0)
        print("   (roundtrip) sending steering = +250 deg and a TEST curb 0.30 m behind the car ...")

        def post(path, obj):
            req = urllib.request.Request(base + path, data=json.dumps(obj).encode(), method="POST", headers={"Content-Type": "application/json", **({"X-Api-Key": a.token} if a.token else {})})
            urllib.request.urlopen(req, timeout=2).read()
        t_send = time.time()
        post("/api/v1/ingest/steering", {"steeringWheelDeg": 250, "gear": "R", "speedKph": 1})
        post("/api/v1/ingest/vision", {"sides": [{"side": "rear", "detected": True, "distanceM": 0.30, "confidence": 1.0}]})
        t_end = time.time() + 3
        while time.time() < t_end and len(rt) < 2:
            with lock:
                for ts, m in got["steering"]:
                    if ts >= t_send and abs(m["steeringWheelDeg"] - 250) < 1 and "s" not in rt: rt["s"] = ts - t_send
                for ts, m in got["curb"]:
                    if ts >= t_send and "c" not in rt and any(z["pos"] == "REAR" and z["zone"] == "DANGER" for z in m["zones"]): rt["c"] = ts - t_send
            time.sleep(0.02)

    time.sleep(max(0.0, a.seconds - (1.0 if a.roundtrip else 0)))
    stop.set()
    s_msgs, c_msgs = got["steering"], got["curb"]
    check(len(s_msgs) > 0, "received %d steering message(s)  (~%.1f/s)" % (len(s_msgs), len(s_msgs) / a.seconds))
    check(len(c_msgs) > 0, "received %d curb message(s)  (~%.1f/s)  - zones appear once the video loop (detector) or a TEST curb produces a result" % (len(c_msgs), len(c_msgs) / a.seconds))
    seqs = [m["seq"] for _, m in s_msgs]
    check(all(b > a_ for a_, b in zip(seqs, seqs[1:])), "steering seq strictly increasing")
    if s_msgs:
        print("\n== 3. Steering content (latest message)"); validate_steering(s_msgs[-1][1])
        biggest = max((m for _, m in s_msgs), key=lambda m: abs(m["steeringWheelDeg"]))
        if biggest is not s_msgs[-1][1] and abs(biggest["steeringWheelDeg"]) > 20:
            print("   (largest steer seen: %.0f deg)" % biggest["steeringWheelDeg"]); validate_steering(biggest)
    if c_msgs:
        print("\n== 4. Curb content"); validate_curb(c_msgs[-1][1])
        zones_seen = {z["zone"] for _, m in c_msgs for z in m["zones"]}
        print("   zones seen during test:", sorted(zones_seen))
    if a.roundtrip:
        print("\n== 5. Round trip (POST -> SSE)")
        check("s" in rt, "steering change reached the stream" + (" in %.0f ms" % (rt["s"] * 1000) if "s" in rt else " - NOT seen within 3 s (is the server running?)"))
        check("c" in rt, "TEST curb 0.30 m (rear) became a DANGER zone on the stream" + (" in %.0f ms" % (rt["c"] * 1000) if "c" in rt else " - NOT seen within 3 s"))
        if "s" in rt: check(rt["s"] < 0.5, "steering latency < 500 ms")
    print("\nRESULT: %d passed, %d failed" % (len(passes), len(fails)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
