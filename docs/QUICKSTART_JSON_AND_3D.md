# Quickstart: see the JSON, then drive the 3D model

## What is already proven (from your screenshots)
Middleware running, SSE connected, steering #491 `valid: yes`, 4-camera panorama with red/yellow/green zones on the right strips,
wheels and steering wheel turning the right way (right turn: right wheel turns more, steering-wheel tick rotated clockwise).
We also checked `vehicle-profile.json` against the real `2025_volkswagen_golf_r.glb`: all 20 node names exist, every pivot is within 2 cm
of its part, and a positive angle means a LEFT turn for both wheels and the steering wheel. So the 3D data is correct.

`steering: 1/s, curb: 1/s` is normal: the server sends a message when something changes, plus one heartbeat per second when nothing moves.
Move a slider (or run `fake_vehicle.py`) and the rate goes up.

## Part 1 - Where is the JSON? (open these in the PC browser)

| URL | What you get |
|---|---|
| `http://localhost:9091/api/v1/steering` | newest steering message (wheel angles, guide lines, 3D transforms) |
| `http://localhost:9091/api/v1/curb` | newest curb message (all zones, colours, pixel positions) |
| `http://localhost:9091/api/v1/profile` | the 3D-model data: node names, pivots, axes, signs (= `vehicle-profile.json`) |
| `http://localhost:9091/meta` | panorama size + where each camera sits |
| `http://localhost:9091/api/v1/signals` | the LIVE stream (SSE). The browser shows text that keeps growing. This is not a file, it never ends; the phone reads it. |

The two grey boxes in `/wp5/viewer` also show the newest raw JSON. Real examples are in `docs/samples/` (`steering_example.json`, `curb_example.json`, `meta_example.json`).

Important: the middleware sends only **numbers**. The 3D model itself is the file `tools/2025_volkswagen_golf_r.glb`; it must be **inside the Android app** (e.g. `app/src/main/assets/`).

## Part 2 - What Android receives (shortened)

```
event: steering
data: {"seq":3,"valid":true,"steeringWheelDeg":300.0,"leftWheelDeg":22.15,"rightWheelDeg":18.217,"direction":"LEFT","gear":"R",
       "guideLeft":[[0.0,0.895],...], "model":{"transforms":[
         {"key":"steeringWheel","node":"R:Int_SW_...","axis":[0,0.372,-0.928],"angleDeg":300.0,"quat":[0,0.186,-0.464,-0.866]},
         {"key":"wheelFrontLeft", "axis":[0,1,0],"angleDeg":22.15,"quat":[0,0.192,0,0.981]},
         {"key":"wheelFrontRight",...}]}}

event: curb
data: {"seq":..,"zones":[{"pos":"REAR_CENTER","zone":"DANGER","color":"#FF3B30","distanceM":0.35,
        "pano":{"x":1331,"w":218,"xNorm":0.574,"wNorm":0.094}}, ...],"summary":{"worstZone":"DANGER",...}}
```
`quat` is `[x,y,z,w]`: the ready-made rotation for that part. The pivot (rotation centre) and ALL node names of each part
(the steering wheel has 8 nodes, each wheel 6) come once from `/api/v1/profile`.

## Part 3 - Connect the 3D model, in 6 small steps (one at a time, check each)

1. **Network** - phone browser opens `http://<PC-IP>:9091/wp5/viewer` (PC IP is printed by the server; firewall rule in WP5_TESTING_GUIDE.md). Not working = nothing else will.
2. **Receive** - add `Wp5Client.kt`, start it, and `Log.d("WP5", it.toString())` inside `client.steering.collect {}`. You must see angles change when you move the slider in the PC viewer.
3. **Model in the app** - put the GLB in `assets/`, show it in your 3D view, scale the model root by **100** (profile `unitScaleToMetres`), because the pivots are in metres.
4. **Load the profile once** - `GET /api/v1/profile` -> `WheelRig(json)`. Log `rig.bindings.keys`: expect `steeringWheel, wheelFrontLeft, wheelFrontRight`.
5. **Turn only the front-left wheel first** - each frame: `rig.update(steering)`, then for every node name in `bindings["wheelFrontLeft"].nodes` set its transform to `delta * restWorld` (`WheelRig.deltaMatrix/apply`). Slider +300 in the PC viewer: the wheel must yaw to the car's LEFT around its own centre.
6. **Add the other two bindings** (front-right wheel, steering wheel), then put `CurbOverlayView` over the panorama.

If something is wrong: wheel turns the wrong way -> change `sign` to -1 in `vehicle-profile.json`. Wheel swings around a wrong point -> forgot the x100 scale or you used the local instead of the world matrix. Nothing moves -> node name not found (names are case-sensitive and start with `R:`).

How the node transform is set depends on the 3D library of your app (Filament, SceneView, Sceneform, raw OpenGL, Unity ...). The math is identical, only the one call differs.
