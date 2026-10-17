# WP-5 Interactive Parking Support — Middleware → Android signal contract

Two signal packages, one live stream.

| Package | SSE event name | Purpose |
|---|---|---|
| 1. Steering + wheels | `steering` | steering-wheel angle, per-wheel steer angles, parking guide lines |
| 2. Curb zones | `curb` | RED / YELLOW / GREEN zone per side (left / right / front / rear), found in the camera frames, mapped to panorama pixels |

## 1. Connect (Android)

`GET http://<middleware-ip>:<port>/api/v1/signals` (Server-Sent Events, same port as `/stitch`).
Optional filter: `?topics=steering` or `?topics=curb`. On connect you immediately receive the latest state of each topic, then live updates. A `: ping` comment arrives every 10 s when idle; ignore it, but if *nothing* arrives for 15 s, reconnect.

Polling fallback / debugging: `GET /api/v1/steering`, `GET /api/v1/curb` (204 = nothing yet).
Browser debug page: `http://<middleware-ip>:<port>/wp5`.

## 2. Package 1 — `steering` message

```json
{"type":"steering","v":1,"seq":39,"ts":1791187699169,"valid":true,
 "steeringWheelDeg":393.883,"roadWheelAvgDeg":25.412,
 "leftWheelDeg":28.939,"rightWheelDeg":22.610,
 "curvature":0.176,"turnRadiusM":5.683,"direction":"LEFT",
 "gear":"R","speedKph":1.500,"frame":"rear_axle_x_fwd_y_left_m",
 "guideLeft":[[0.000,0.925],[-0.380,0.940]],
 "guideRight":[[0.000,-0.925],[-0.380,-0.940]]}
```
(guide arrays are shortened here; real messages carry 12 points each.)

**Sign convention (ISO 8855): positive = LEFT turn (counter-clockwise seen from above).** All angles in degrees.

| Field | Meaning | Drive in the 3D model |
|---|---|---|
| `steeringWheelDeg` | angle of the driver's steering wheel, clamped to ±max | rotate the *steering-wheel node* around the column axis |
| `leftWheelDeg` / `rightWheelDeg` | front-left / front-right road-wheel steer angle (Ackermann: inner wheel turns more) | rotate each front wheel's *steer-pivot node* (yaw), NOT the spin node |
| `roadWheelAvgDeg` | average road-wheel angle | optional (single-wheel models, HUD text) |
| `direction`, `turnRadiusM`, `curvature` | LEFT/RIGHT/STRAIGHT, radius in m (0 = straight) | HUD text, arrow |
| `gear` | `P` `R` `N` `D` | show reverse guide lines only when `R` |
| `guideLeft` / `guideRight` | predicted path of the left/right body edge, `[x,y]` metres, **origin = rear-axle centre, +x forward, +y left**. In `R` the points go to negative x | draw as a polyline on the ground plane (parking guide lines) |
| `valid` | `false` = vehicle-bus data is stale (>2 s old). Grey out / hide the overlay | |
| `seq`, `ts` | monotonically increasing counter, server epoch-ms | drop out-of-order messages (`seq` lower than last) |

**Update rate:** event-driven (every change of at least 0.5 degrees, typically up to ~20/s) plus a 1 s heartbeat. Because that is not 60 fps, **interpolate on the phone**: store the latest value as a *target* and move the node toward it every frame (e.g. `current += (target - current) * 0.35f`). The wheels then move smoothly instead of jumping.

## 3. Package 2 — `curb` message

Zones are measured **from the content of the camera frames** (curb detector on the server). There are no distance sensors. A side whose camera frame shows no curb has **no zone entry** (nothing is drawn).

```json
{"type":"curb","v":2,"seq":12,"ts":1791187699169,
 "panorama":{"width":2320,"height":360},
 "thresholds":{"dangerM":0.500,"warningM":1.000},
 "zones":[
  {"id":"CURB_LEFT","pos":"LEFT","kind":"CURB","source":"CAMERA","confidence":0.82,
   "distanceM":0.730,"zone":"WARNING","level":2,"color":"#FFCC00",
   "camera":"left","pano":{"x":1680,"w":640,"xNorm":0.724,"wNorm":0.276}}],
 "summary":{"worstZone":"WARNING","worstColor":"#FFCC00",
            "nearestDistanceM":0.730,"nearestZoneId":"CURB_LEFT",
            "leftWorst":"WARNING","rightWorst":"NONE","frontWorst":"NONE","rearWorst":"NONE"},
 "vision":{"mode":"camera","left":{"detected":true,"confidence":0.82,"distanceM":0.73,"source":"CAMERA",
           "line":{"x1":1700,"y1":267,"x2":2300,"y2":267}},"right":{"detected":false},
           "front":{"detected":false},"rear":{"detected":false}}}
```

`pos` is `LEFT`, `RIGHT`, `FRONT` or `REAR` (one zone per side, covering the whole strip of that camera). `source` is `CAMERA` (detector) or `TEST` (typed in by hand in the viewer). `distanceM` = metres from the car body to the curb. `vision.<side>.line` = the curb line found, in panorama pixels (draw it to show the driver what was detected).

| Zone | level | colour | default rule |
|---|---|---|---|
| `DANGER` | 3 | red `#FF3B30` | distance <= 0.50 m |
| `WARNING` | 2 | yellow `#FFCC00` | distance <= 1.00 m |
| `SAFE` | 1 | green `#34C759` | curb detected, but farther than 1.00 m |
| `NONE` | 0 | grey `#9E9E9E` | only used in `summary.*Worst` = "no curb in that frame" (there is no zone entry) |

Hysteresis (5 cm) is applied in the middleware, so colours do not flicker at a threshold. A detection must be seen in 4 of the last 6 analysed frames before it appears, and disappears when it is gone; a result nobody refreshes for 1 s (detector stopped) is removed.

**Drawing on the panorama:** `pano.x` / `pano.w` are pixels in the stitched panorama (`panorama.width` x `height`, the same as `/meta`). `xNorm` / `wNorm` are the same values as 0..1 fractions; use them when the Android view scales the image.

**Context-aware emphasis (phone position):** the phone decides, the middleware only supplies data. Use `summary.leftWorst` / `rightWorst` / `frontWorst` / `rearWorst` for per-side focus, and `nearestDistanceM` / `nearestZoneId` for near-field emphasis (e.g. pulse the nearest zone, dim the others).

## 4. Getting data INTO the middleware

```
POST /api/v1/ingest/steering   {"steeringWheelDeg": -135.0, "gear": "R", "speedKph": 1.2}          (vehicle gateway)
POST /api/v1/ingest/vision     {"sides":[{"side":"front","detected":true,"distanceM":0.42}]}      (TEST only)
```
Only the steering angle comes from the vehicle. Curb zones need **no input**: the server analyses the camera frames by itself. `ingest/vision` only exists to test the apps without a real curb in the clips: `side` is `left`, `right`, `front` or `rear`; a TEST value beats the detector for 1.5 s, so send it at least every 1 s.

Real vehicle: your CAN/Ethernet reader decodes the steering-angle signal using the car's DBC file, converts it to degrees with **+ = left** (if your bus uses + = right, negate it there, once), and calls the first endpoint. You can also call `Wp5Service.onSteeringInput(...)` directly from Java.

No vehicle yet? Start the server with `-Dwp5.simulate=true`: a fake car holds the steering at `-Dwp5.sim.steerDeg` (default 0) or sweeps +-450 degrees with `-Dwp5.sim.sweep=true`. Never enable this in a real vehicle.

## 5. Settings (JVM flags, `-Dwp5.<name>=value`)

`wheelbaseM` 2.70, `trackWidthM` 1.60, `vehicleWidthM` 1.85, `steeringRatio` 15.5, `maxSteeringWheelDeg` 540, `deadbandDeg` 0.5, `heartbeatMs` 1000, `pathLengthM` 5, `pathPoints` 12, `dangerM` 0.5, `warningM` 1.0, `maxRangeM` 2.5, `hysteresisM` 0.05, `visionStaleMs` 1000, `ingestToken` (if set, ingest calls need the header `X-Api-Key: <token>`).

**Curb detector flags** (`-Dwp5.vision.<name>=value`, or per camera `-Dwp5.vision.<front|rear|left|right>.<name>=value`): `detector` (true/false), `sides` (default `left,right,front,rear`), `everyNthFrame` 3, `roiTop` 0.45, `roiBottom` 0.92 (front/rear 0.82), `minScore` 0.55, `rowAt0m` / `rowAtFar` / `farM` (row to metres calibration), `window` 6, `need` 4. Calibrate `rowAt0m`, `rowAtFar` and `farM` **per camera** with real footage: the defaults are placeholders.

**The defaults are generic. Replace wheelbase, track, width and steering ratio with your vehicle's real values.**

With Maven: `mvn exec:java "-Dexec.mainClass=com.middleware.panorama.CameraStreamingServer" "-Dexec.args=9091 0" "-Dwp5.simulate=true"`

## 6. Kotlin client sketch (OkHttp SSE)

```kotlin
// build.gradle: implementation("com.squareup.okhttp3:okhttp:4.12.0")
//               implementation("com.squareup.okhttp3:okhttp-sse:4.12.0")
val client = OkHttpClient.Builder().readTimeout(0, TimeUnit.MILLISECONDS).build() // no timeout for SSE
val req = Request.Builder().url("http://$host:$port/api/v1/signals").build()

val source = EventSources.createFactory(client).newEventSource(req, object : EventSourceListener() {
    override fun onEvent(es: EventSource, id: String?, type: String?, data: String) {
        val j = JSONObject(data)
        when (type) {
            "steering" -> {
                if (j.getLong("seq") <= lastSteeringSeq) return        // out-of-order
                lastSteeringSeq = j.getLong("seq")
                target.steeringWheel = j.getDouble("steeringWheelDeg").toFloat()
                target.wheelL = j.getDouble("leftWheelDeg").toFloat()
                target.wheelR = j.getDouble("rightWheelDeg").toFloat()
                target.valid = j.getBoolean("valid")
            }
            "curb" -> curbZones = parseZones(j)  // draw on panorama with xNorm/wNorm + color
        }
    }
    override fun onFailure(es: EventSource, t: Throwable?, r: Response?) { scheduleReconnect() }
})

// every frame (render loop): smooth toward target
current.steeringWheel += (target.steeringWheel - current.steeringWheel) * 0.35f
```
Phone on Wi-Fi: use the PC/gateway IP. Android emulator: `10.0.2.2` is the host PC. Cleartext HTTP needs `android:usesCleartextTraffic="true"` (or a network-security-config) in the manifest. On Windows, allow the port through the firewall.

## 7. Camera mapping (VERIFY on the real rig)

The server names the four cameras `front`, `rear`, `left`, `right` (`-Dpanels=1:front,4:right,2:rear,3:left`). Each side's zone lives on the strip of the camera with the same name, and that camera's frame is the one analysed for that side. If a clip is wired to the wrong role, the zone shows up on the wrong side: fix `-Dpanels`.

## 8. Customisation: `vehicle-profile.json` and the `model` block

Everything that depends on the 3D model or the car is in ONE JSON file, so nothing needs recompiling.

**Where it is loaded from** (first match wins): `-Dwp5.profile=<path>`, then `vehicle-profile.json` in the working directory, then built-in defaults. The server prints which one it used at start (`[WP5] profile: ... (from ...)`). If the file is broken it prints a loud `!!!` error and falls back to the defaults. Start-up never crashes, but the node names will then not match your model.

**Priority for every number:** `-Dwp5.<name>` flag, then profile file, then built-in default.

### Sections

| Section | Content |
|---|---|
| `model` | `file`, `frame` (`forward`/`left`/`up` = which MODEL axis is which vehicle direction, e.g. `"+Z"`), `rearAxleOriginM` = rear-axle centre at ground level in MODEL coordinates |
| `vehicle` | `wheelbaseM trackWidthM vehicleWidthM steeringRatio maxSteeringWheelDeg` |
| `steering` | `deadbandDeg heartbeatMs pathLengthM pathPoints` |
| `zones` | `dangerM warningM maxRangeM hysteresisM` |
| `colors` | `DANGER WARNING SAFE NONE` as `#RRGGBB` |
| `bindings` | one entry per 3D node to rotate (see below) |

### A binding

```json
"wheelFrontLeft": {
  "node": "FL_steer_pivot",        // exact node name in the .glb
  "source": "leftWheelDeg",        // steeringWheelDeg | leftWheelDeg | rightWheelDeg | roadWheelAvgDeg
  "axis": [0, 1, 0],               // rotation axis in the node's parent space (Y = up in glTF)
  "sign": 1,                       // -1 flips the direction
  "offsetDeg": 0,                  // added after sign (calibrates a rest pose that is not straight)
  "minDeg": -45, "maxDeg": 45      // hard limits
}
```
`angle = clamp(sign * value + offsetDeg, minDeg, maxDeg)`. Add as many bindings as you like (e.g. a speedometer needle, rear-wheel steer): the `steering` message will contain one transform per binding.

### The `model` block inside every `steering` message

```json
"model": {
  "profile": "golf-r-2025", "modelFile": "2025_volkswagen_golf_r.glb",
  "units": "deg", "quatOrder": "xyzw",
  "transforms": [
    {"key":"wheelFrontLeft","node":"FL_steer_pivot","axis":[0,1,0],
     "angleDeg":30.036,"angleRad":0.524,"quat":[0,0.259,0,0.966]}
  ],
  "guideLeft":  [[0.895,0,0],[2.71,0,-3.573]],   // guide line in MODEL coordinates [x,y,z], z=ground
  "guideRight": [[...]]
}
```
Android: `node.localRotation = restRotation * quat` where `restRotation` is the rotation stored in the .glb for that node (read it once at load; do not assume identity).
**For smooth motion prefer `angleDeg`:** smooth the angle, then build the quaternion yourself. `quat` flips sign beyond +-360 degrees (steering wheel), which breaks quaternion interpolation. `GET /api/v1/profile` returns the active profile, so Android can read node names instead of hard-coding them.

### How to fill the profile from the .glb (step by step)

1. `python tools/glb_inspect.py 2025_volkswagen_golf_r.glb` (Python 3, no packages). It prints the node tree, steering-wheel and wheel candidates, a pivot check per wheel, model size, **wheelbase, track width and rear-axle origin measured from the model**.
2. `python tools/glb_inspect.py 2025_volkswagen_golf_r.glb --write-profile vehicle-profile.json` writes a starter file with those numbers. Every `TODO` must be checked by hand.
3. Choose the right node per part: the **steer** node of each front wheel (origin at the wheel centre, contains tyre + rim, NOT the node that spins the wheel), and the steering-wheel node.
4. Put the file next to where you start the server and restart. Look at the model in Android. If a part turns the wrong way, flip its `sign`; if the steering wheel's axis is wrong, adjust `axis` (a real column is tilted, e.g. `[0, 0.36, -0.93]`).
5. Real-vehicle `steeringRatio` and `maxSteeringWheelDeg`: take from VW data / measure (max steering-wheel angle / max front-wheel angle).


### Pivots, node groups and scale (added for the Golf R model)

`tools/make_profile.py` measures these from the .glb and writes them into `vehicle-profile.json`; `GET /api/v1/profile` serves them unchanged.

| Field | Meaning |
|---|---|
| `model.unitScaleToMetres` | multiply the loaded model by this so 1 unit = 1 m (the Golf GLB is 100x too small as exported) |
| `bindings.<key>.nodes` | ALL node names that move together (steering wheel = 8 meshes, front wheel = tyre + rim + brake parts) |
| `bindings.<key>.pivotM` | point to rotate around, scene-world frame in metres. The wheel nodes' own origin is at the car origin, NOT at the wheel centre |
| `bindings.<key>.axis` | rotation axis, scene-world frame (steering column is tilted: about `[0, 0.372, -0.928]`) |

Apply on Android:  `newWorld = T(pivot) * R(axis, angleDeg) * T(-pivot) * restWorld`, for every node in `nodes`. The `steering` message supplies `angleDeg` per binding `key`.


## 9. Testing without the Android app

`/wp5/viewer` (browser stand-in with panorama overlay, top view and fake-vehicle sliders), `tools/fake_vehicle.py` (scripted gateway), `tools/check_signals.py` (automatic checks incl. round-trip latency). Step-by-step: `docs/WP5_TESTING_GUIDE.md`.
