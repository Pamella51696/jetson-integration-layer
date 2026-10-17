# WP5 — run it with the 4 videos and prove the Android side works

The 4 videos carry the **pictures**, and the curb zones are measured **from those pictures** (front, rear, left, right).
Only the steering angle is a *signal* from the vehicle (later) or from a fake vehicle (now); it uses the same ingest call
either way, so nothing changes in the middleware or the app when the real CAN reader replaces the fake one.
The sample clips contain no curb, so to see zones use the viewer's "Curb in the frame (TEST)" boxes or `fake_vehicle.py`.

```
 4 videos ──► stitch ──► /stitch (MJPEG panorama) ───────────────┐
 4 camera frames ──► curb detector ─────────────────────────────┐
 fake vehicle / CAN ──► POST /api/v1/ingest/steering ──► WP5 ◄──────┘──► /api/v1/signals (SSE) ──► Android
                                                               (zones, Ackermann, guide lines, 3D transforms)
```

## 1. Start the middleware with the 4 videos (PowerShell, from the project root)

```powershell
mvn clean compile
mvn exec:java "-Dexec.mainClass=com.middleware.panorama.VideoStreamingServer" "-Dexec.args=9091"
```
Note: `exec:java` has **no space** after the colon. Do not add `-Dwp5.simulate=true` when you use `fake_vehicle.py` (both would set the steering).

* **Panel order** (left to right in the panorama) and the role of each clip are set by `-Dpanels`, default `1:front,4:right,2:rear,3:left`. This is the ring order around the car (neighbouring panels touch; the left panel wraps round to the front). The role names must be exactly front / rear / left / right, each once: WP5 uses them to put the curb zones on the right strip. If your clips are different, change the numbers, e.g. `"-Dpanels=2:front,4:right,3:rear,1:left"`.
* The panel whose role is `rear` automatically gets `RearFeedPipeline` (look up 18 degrees and crop the bumper). Before, that was hard-coded to panel 3, which was the wrong clip.
* Videos are looked up as `<number>.mp4` in `-Dvideo.dir`, then `src/main/java`, then the current folder (`-Dvideo.ext=avi` for another extension). Four explicit paths after the port also work (panel order).
* Console must show the four `panel n: role <- file` lines and `[WP5] profile: 2025-volkswagen-golf-r`.
* Playback is paced to the clips' fps (30). `-Dvideo.realtime=false` = as fast as possible.

## 2. Test ladder (stop at the first step that fails)

| # | Test | Command | Pass looks like |
|---|---|---|---|
| 1 | Server answers | `curl http://localhost:9091/api/v1/profile` | JSON with `"profileName":"2025-volkswagen-golf-r"` |
| 2 | Drive fake vehicle | `python tools/fake_vehicle.py --port 9091` | console prints steering angle + test-curb distances, loops every 30 s |
| 3 | See it in a browser (Android stand-in) | open `http://localhost:9091/wp5/viewer` | curb zones on the car in the top view (all 4 sides), wheels turn, wheel icon rotates |
| 4 | Automatic acceptance test | `python tools/check_signals.py --port 9091 --roundtrip` *(stop step 2 first)* | `RESULT: 38 passed, 0 failed`, latency in ms |
| 5 | Video speed | `python tools/measure_fps.py --port 9091` | close to 30 fps (the clips' rate); far lower = slow-motion playback |
| 6 | Real phone | see section 3 | same picture as step 3, on the phone |

In the viewer tick **Send steering** to use the steering slider instead of `fake_vehicle.py`, and tick a side under **Curb in the frame (TEST)** to put a curb on that side.
Things to verify by eye: (a) positive angle = left turn and the inner wheel turns more; (b) a test curb on left / right / front / rear draws 3 dotted lines on that side of the car; (c) red <= 0.5 m, yellow <= 1.0 m, green beyond; (d) untick the curb: the zone disappears (no curb in the frame = nothing shown) and steering shows **STALE** 2 s after you stop sending.

## 3. Connect the Android app

Android base URL: `http://<PC-IP>:9091` (the server prints `Network access -> http://<ip>:9091/play`). SSE = `/api/v1/signals`, 3D data once at start = `/api/v1/profile`, camera layout = `/meta`, video = `/stitch`.

| Phone | Use | Notes |
|---|---|---|
| Real phone, same Wi-Fi as the PC | `http://<PC-IP>:9091` | open the port once (admin PowerShell): `netsh advfirewall firewall add rule name="WP5 9091" dir=in action=allow protocol=TCP localport=9091` |
| Real phone, USB cable | `adb reverse tcp:9091 tcp:9091`, then use `http://localhost:9091` | no Wi-Fi/firewall needed |
| Emulator | `http://10.0.2.2:9091` | |

Manifest needs `INTERNET` and `android:usesCleartextTraffic="true"` (plain http). Quick phone check without any code: open `http://<PC-IP>:9091/wp5/viewer` in the phone browser. If that works, network and middleware are fine and any remaining problem is in the app.

Full step-by-step (layout, SSE client, 3D model hook-up, checks): `docs/ANDROID_STEP_BY_STEP.md`.

Reference code in `android/`: `Wp5Client.kt` (SSE with reconnect, out-of-order drop, parsed models), `CurbOverlayView.kt` (zones on top of the panorama view), `WheelRig.kt` (reads `/api/v1/profile`, smooths, builds the pivot matrices for the 3D model). They are **not compiled in this project**; treat them as a starting point.

## 4. Signal mapping cheat sheet (full detail in WP5_SIGNAL_API.md)

| Signal | Field | 3D / UI action |
|---|---|---|
| steering wheel | `steeringWheelDeg` (+ = left) | rotate `bindings.steeringWheel.nodes` about `axis` around `pivotM` |
| front wheels | `leftWheelDeg`, `rightWheelDeg` | rotate `wheelFrontLeft/Right.nodes` (yaw) around their `pivotM` |
| guide lines | `guideLeft/Right` `[x fwd, y left]` m from rear axle | ground polyline, show when `gear == "R"` |
| curb zone | `zones[].color`, `pano.xNorm/wNorm` | zone lines for that side (left / right / front / rear) |
| context emphasis | `summary.leftWorst/rightWorst/frontWorst/rearWorst/nearestZoneId` | phone decides what to highlight |

Smoothing: messages come at about 20/s, screens render 60 fps, so ease toward the latest target every frame (`cur += (target-cur)*0.35`). Compare with the viewer's top view, which does exactly that.

## 5. Going real later

Replace `fake_vehicle.py` by your CAN reader that calls the steering endpoint (or `Wp5Service.onSteeringInput(...)` in Java). Enter the real `steeringRatio` and `maxSteeringWheelDeg` in `vehicle-profile.json` (marked CHECK BY HAND in `make_profile.py` output). Calibrate the curb detector per camera (`-Dwp5.vision.<front|rear|left|right>.rowAt0m` etc., see WP5_SIGNAL_API.md section 5) on real footage that contains curbs. Set `-Dwp5.ingestToken=...` before the gateway is reachable by anyone else.
