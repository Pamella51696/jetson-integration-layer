package com.example.seethrough.wp5

import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import okhttp3.*
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

// build.gradle:  implementation("com.squareup.okhttp3:okhttp:4.12.0")
//                implementation("com.squareup.okhttp3:okhttp-sse:4.12.0")
//                implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
// AndroidManifest: <uses-permission android:name="android.permission.INTERNET"/>
//                  <application android:usesCleartextTraffic="true" ...>   (plain http on the LAN)

/** Package 1: everything the 3D overlay needs. Angles in degrees, + = LEFT. */
data class SteeringSignal(
    val seq: Long, val valid: Boolean, val gear: String, val direction: String,
    val steeringWheelDeg: Float, val leftWheelDeg: Float, val rightWheelDeg: Float,
    val turnRadiusM: Float,
    /** parking guide lines, vehicle frame [x forward, y left] metres, origin = rear-axle centre */
    val guideLeft: List<FloatArray>, val guideRight: List<FloatArray>,
)

enum class Zone(val level: Int) { NONE(0), SAFE(1), WARNING(2), DANGER(3) }

/** One curb zone of package 2 (pos = LEFT | RIGHT | FRONT | REAR, found in that camera's frame). xNorm/wNorm are 0..1 of the panorama width. */
data class CurbZone(
    val id: String, val pos: String, val zone: Zone, val colorArgb: Int,
    val distanceM: Float?, val xNorm: Float, val wNorm: Float,
)

data class CurbSignal(val seq: Long, val zones: List<CurbZone>, val worst: Zone, val nearestId: String?,
                      val leftWorst: Zone, val rightWorst: Zone, val frontWorst: Zone, val rearWorst: Zone)

class Wp5Client(private val host: String, private val port: Int, private val scope: CoroutineScope) {

    private val _steering = MutableStateFlow<SteeringSignal?>(null)
    private val _curb = MutableStateFlow<CurbSignal?>(null)
    val steering: StateFlow<SteeringSignal?> = _steering
    val curb: StateFlow<CurbSignal?> = _curb
    val connected = MutableStateFlow(false)

    private val http = OkHttpClient.Builder().readTimeout(0, TimeUnit.MILLISECONDS).build()   // SSE: no read timeout
    private var source: EventSource? = null
    private var watchdog: Job? = null
    @Volatile private var lastRx = 0L
    private var lastSteerSeq = -1L
    private var retryMs = 500L
    private var stopped = false

    fun start() { stopped = false; open() }
    fun stop() { stopped = true; watchdog?.cancel(); source?.cancel(); connected.value = false }

    private fun open() {
        source?.cancel()
        val req = Request.Builder().url("http://$host:$port/api/v1/signals").build()
        source = EventSources.createFactory(http).newEventSource(req, object : EventSourceListener() {
            override fun onOpen(es: EventSource, response: Response) { connected.value = true; retryMs = 500; lastRx = now() }
            override fun onEvent(es: EventSource, id: String?, type: String?, data: String) {
                lastRx = now()
                try {
                    when (type) {
                        "steering" -> parseSteering(JSONObject(data))?.let { _steering.value = it }
                        "curb" -> _curb.value = parseCurb(JSONObject(data))
                    }
                } catch (e: Exception) { android.util.Log.w("WP5", "bad $type message: $e") }  // never crash the UI thread
            }
            override fun onFailure(es: EventSource, t: Throwable?, response: Response?) { connected.value = false; reconnectLater() }
            override fun onClosed(es: EventSource) { connected.value = false; reconnectLater() }
        })
        watchdog?.cancel()
        watchdog = scope.launch { while (isActive) { delay(5000); if (now() - lastRx > 15_000) { open(); return@launch } } } // server pings every 10 s
    }

    private fun reconnectLater() {
        if (stopped) return
        scope.launch { delay(retryMs); retryMs = (retryMs * 2).coerceAtMost(5000); if (!stopped) open() }
    }

    private fun now() = System.currentTimeMillis()

    private fun parseSteering(j: JSONObject): SteeringSignal? {
        val seq = j.getLong("seq")
        if (seq <= lastSteerSeq && seq > lastSteerSeq - 5) return null      // duplicate / out of order
        lastSteerSeq = seq
        return SteeringSignal(
            seq, j.getBoolean("valid"), j.getString("gear"), j.getString("direction"),
            j.getDouble("steeringWheelDeg").toFloat(), j.getDouble("leftWheelDeg").toFloat(),
            j.getDouble("rightWheelDeg").toFloat(), j.optDouble("turnRadiusM", 0.0).toFloat(),
            pts(j.getJSONArray("guideLeft")), pts(j.getJSONArray("guideRight")))
    }

    private fun pts(a: JSONArray) = List(a.length()) { i -> a.getJSONArray(i).let { floatArrayOf(it.getDouble(0).toFloat(), it.getDouble(1).toFloat()) } }

    private fun parseCurb(j: JSONObject): CurbSignal {
        val za = j.getJSONArray("zones")
        val zones = List(za.length()) { i ->
            val z = za.getJSONObject(i); val p = z.getJSONObject("pano")
            CurbZone(z.getString("id"), z.getString("pos"), Zone.valueOf(z.getString("zone")),
                android.graphics.Color.parseColor(z.getString("color")),
                if (z.isNull("distanceM")) null else z.getDouble("distanceM").toFloat(),
                p.getDouble("xNorm").toFloat(), p.getDouble("wNorm").toFloat())
        }
        val s = j.getJSONObject("summary")
        return CurbSignal(j.getLong("seq"), zones, Zone.valueOf(s.getString("worstZone")),
            if (s.isNull("nearestZoneId")) null else s.getString("nearestZoneId"),
            Zone.valueOf(s.getString("leftWorst")), Zone.valueOf(s.getString("rightWorst")),
            Zone.valueOf(s.getString("frontWorst")), Zone.valueOf(s.getString("rearWorst")))
    }
}
