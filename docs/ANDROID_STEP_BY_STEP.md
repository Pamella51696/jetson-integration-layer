# Android: from the middleware to the 3D model, step by step

Everything below uses only what the middleware already serves. Do the steps in order and stop at the first one that fails.
The Kotlin in `android/` is a **reference that has not been compiled** by us - expect small fixes for your project.

## 0. Middleware side (PC)

1. `mvn clean compile`
2. `mvn exec:java "-Dexec.mainClass=com.middleware.panorama.VideoStreamingServer" "-Dexec.args=9091"`
3. Console must show `[WP5] profile: 2025-volkswagen-golf-r` and the four panels `front, right, rear, left`.
4. Sender of the signals (until the real vehicle bus exists): `python tools/fake_vehicle.py --port 9091`
5. Prove it in a browser first: `http://localhost:9091/wp5/viewer` (zones on the panorama, wheels turning).

## 1. Make the phone reach the PC

| Phone | Base URL | Extra |
|---|---|---|
| real phone, same Wi-Fi | `http://<PC-IP>:9091` (the console prints the IP) | admin PowerShell, once: `netsh advfirewall firewall add rule name="WP5 9091" dir=in action=allow protocol=TCP localport=9091` |
| real phone, USB | `http://localhost:9091` | `adb reverse tcp:9091 tcp:9091` |
| emulator | `http://10.0.2.2:9091` | |

Test **before writing any app code**: open `<base URL>/wp5/viewer` in the phone's browser. If it works, network + middleware are fine.

## 2. Android project setup

`app/build.gradle(.kts)`:
```
implementation("com.squareup.okhttp3:okhttp:4.12.0")
implementation("com.squareup.okhttp3:okhttp-sse:4.12.0")
implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
```
`AndroidManifest.xml`: `<uses-permission android:name="android.permission.INTERNET"/>` and `android:usesCleartextTraffic="true"` on `<application>` (plain http on the LAN).
Copy `android/Wp5Client.kt`, `CurbOverlayView.kt`, `WheelRig.kt` into your package (fix the `package` line).

## 3. Layout: panorama + zone overlay (aspect ratio matters!)

The overlay uses `xNorm * viewWidth`, so it must have exactly the same bounds as the panorama picture. The panorama is **2320 x 360 (ratio 6.44 : 1)**. Do not let the picture letterbox (`fitCenter` with a different view ratio) or the zones will be shifted.
```xml
<FrameLayout android:layout_width="match_parent" android:layout_height="0dp"
    app:layout_constraintDimensionRatio="2320:360" ...>
    <!-- your existing panorama view, scaleType fitXY, same bounds -->
    <com.example.seethrough.wp5.CurbOverlayView android:id="@+id/curbOverlay"
        android:layout_width="match_parent" android:layout_height="match_parent"/>
</FrameLayout>
```
The panorama itself is the MJPEG stream `<base URL>/stitch` (the same one your app already shows; if you have no MJPEG view yet, a `WebView` on `<base URL>/stitch` is the quickest way to see it).

## 4. Connect and receive signals

```kotlin
class MainActivity : AppCompatActivity() {
    private val BASE = "192.168.1.20"                 // PC IP, or localhost with adb reverse, or 10.0.2.2
    private lateinit var client: Wp5Client
    private var rig: WheelRig? = null

    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        setContentView(R.layout.activity_main)
        client = Wp5Client(BASE, 9091, lifecycleScope)
        client.start()

        // curb zones -> overlay
        lifecycleScope.launch { client.curb.collect { findViewById<CurbOverlayView>(R.id.curbOverlay).signal = it } }

        // profile ONCE (node names, pivots, axes of YOUR glb) -> rig
        lifecycleScope.launch(Dispatchers.IO) {
            val json = java.net.URL("http://$BASE:9091/api/v1/profile").readText()
            rig = WheelRig(json)
        }
        // connection indicator
        lifecycleScope.launch { client.connected.collect { /* show green/red dot */ } }
    }
    override fun onDestroy() { client.stop(); super.onDestroy() }
}
```
**Check now:** with `fake_vehicle.py` running, the curb zones must appear (left / right / front / rear) and follow the test curbs. Put a log in the `curb.collect` block first if nothing shows (`Log.d("WP5", it.toString())`).

## 5. Drive the 3D model (steering wheel + front wheels)

The profile (`/api/v1/profile`, the file `vehicle-profile.json`) tells you, for **your** GLB:
`steeringWheel`, `wheelFrontLeft`, `wheelFrontRight` -> `nodes` (node names), `pivotM` (rotation centre), `axis`, `sign`, `source`.
The GLB is 100 x smaller than metres: scale the model root by `unitScale` (=100) so the metre pivots are valid.

Every rendered frame (not only when a message arrives; messages come ~20/s, the screen runs at 60 fps):
```kotlin
val s = client.steering.value ?: return
val r = rig ?: return
r.update(s)                                   // eases toward the newest angles
for (key in listOf("steeringWheel", "wheelFrontLeft", "wheelFrontRight")) {
    val delta = r.deltaMatrix(key)            // T(pivot) * R(axis, angle) * T(-pivot)
    for (nodeName in r.bindings.getValue(key).nodes) {
        // newWorld = delta * restWorld   (restWorld = the node's world matrix when the model was loaded, saved once)
        // then hand newWorld to your engine
    }
}
```
Engine hand-off (example for Filament / gltfio; check the method names against your version):
```kotlin
val tm = engine.transformManager
val e = asset.getFirstEntityByName(nodeName)
val inst = tm.getInstance(e)
// rest: tm.getWorldTransform(inst, restWorld)  <- once, right after loading, before any rotation
val newWorld = r.apply(key, restWorld)        // delta * restWorld
// setTransform wants LOCAL: local = inverse(parentWorld) * newWorld  (identity parent => local == newWorld)
tm.setTransform(inst, local)
```
Other engines (SceneView, Sceneform, raw OpenGL) work the same way: find the node by name, take its rest world matrix once, multiply the delta, set it. All matrices are column-major `float[16]`.

**Check now (use the viewer as the reference, it shows the right behaviour):**
- steering slider to +300: steering wheel turns **counter-clockwise** (left), both front wheels yaw left, the inner (left) wheel more.
- slider to -300: mirrored.
- Wrong direction => flip `sign` for that binding in `vehicle-profile.json` (no code change). Wheel rotates around the wrong point => wrong `pivotM` or you forgot the 100 x scale. Wheel rotates around the wrong axis => `axis`.

## 6. Guide lines (optional, for reversing)

`steering.guideLeft / guideRight` are polylines `[x forward, y left]` in metres from the rear-axle centre. Draw them on the ground in the 3D scene (y up in the scene = z of nothing: map vehicle x -> scene -z or +z according to your model, as in `vehicle-profile.json` "front = +Z, left = +X"), only while `gear == "R"`.

## 7. Staleness / safety behaviour you must keep

- No curb in a camera frame => no zone entry for that side (nothing is drawn); `summary.<side>Worst` is `NONE`.
- Steering not updated for 2 s => `valid:false`; grey out / hide the overlay.
- The client reconnects by itself (0.5 s ... 5 s back-off) and via a 15 s watchdog; show a "no signal" state while `client.connected` is false.

## 8. Quick triage

| Symptom | Likely cause |
|---|---|
| nothing connects | firewall rule (step 1), wrong IP, missing cleartext flag, VPN on the PC |
| connects but no zones | `fake_vehicle.py` not running, or panorama view bounds differ from the overlay |
| zones in the wrong camera strip | panel order: `-Dpanels` roles must match the physical cameras (see WP5_TESTING_GUIDE.md) |
| zones shifted sideways | panorama letterboxed (aspect ratio not 2320:360) |
| wheels turn the wrong way | `sign` in the profile |
| everything jumpy | `update()` not called every frame / smoothing too high |
