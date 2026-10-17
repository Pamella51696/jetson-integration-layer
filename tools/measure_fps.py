#!/usr/bin/env python3
"""How many panorama frames per second does the middleware really deliver?  (Python 3.8+, stdlib only)
   python tools/measure_fps.py --port 9091 --seconds 10
Compare with the source clips (30 fps). Much lower = clips play in slow motion (see WP5_TESTING_GUIDE.md)."""
import argparse, time, urllib.request
ap = argparse.ArgumentParser(); ap.add_argument("--host", default="localhost"); ap.add_argument("--port", type=int, default=9091); ap.add_argument("--seconds", type=float, default=10)
a = ap.parse_args()
r = urllib.request.urlopen("http://%s:%d/stitch" % (a.host, a.port), timeout=10)
t0 = time.time(); buf = b""; frames = 0; size = 0
while time.time() - t0 < a.seconds:
    chunk = r.read(65536)
    if not chunk: break
    size += len(chunk); buf = (buf + chunk)[-8:]
    frames += chunk.count(b"\xff\xd8\xff")
dt = time.time() - t0
print("%d frames in %.1f s = %.1f fps, %.2f MB/s" % (frames, dt, frames / dt, size / dt / 1e6))
