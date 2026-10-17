package com.example.seethrough.wp5

import android.opengl.Matrix
import org.json.JSONObject

/**
 * Turns a SteeringSignal into world matrices for the Golf R model parts.
 * Engine-agnostic: gives column-major float[16] (OpenGL / glTF / Filament / Sceneform order).
 *
 * From GET /api/v1/profile (call once at start-up) every binding has:
 *   nodes   = ALL node names that move together,   pivotM = rotation centre (scene metres),
 *   axis    = rotation axis (scene frame),         sign / offsetDeg / minDeg / maxDeg.
 * The scene must be scaled by model.unitScaleToMetres (=100 for this GLB) BEFORE these metre pivots are valid.
 *
 *   world(node) = T(pivot) * R(axis, angle) * T(-pivot) * restWorld(node)       <- restWorld = pose from the .glb
 */
class WheelRig(profileJson: String) {

    class Binding(val key: String, val nodes: List<String>, val pivot: FloatArray, val axis: FloatArray,
                  val source: String, val sign: Float, val offsetDeg: Float, val minDeg: Float, val maxDeg: Float) {
        var currentDeg = 0f                       // smoothed value that is actually rendered
    }

    val bindings: Map<String, Binding>
    val unitScale: Float

    init {
        val p = JSONObject(profileJson)
        unitScale = p.getJSONObject("model").optDouble("unitScaleToMetres", 1.0).toFloat()
        val b = p.getJSONObject("bindings"); val out = LinkedHashMap<String, Binding>()
        for (k in b.keys()) {
            val o = b.getJSONObject(k)
            val nodes = o.optJSONArray("nodes")?.let { a -> List(a.length()) { a.getString(it) } } ?: listOf(o.getString("node"))
            out[k] = Binding(k, nodes, f3(o.getJSONArray("pivotM")), f3(o.getJSONArray("axis")), o.getString("source"),
                o.optDouble("sign", 1.0).toFloat(), o.optDouble("offsetDeg", 0.0).toFloat(),
                o.optDouble("minDeg", -360.0).toFloat(), o.optDouble("maxDeg", 360.0).toFloat())
        }
        bindings = out
    }

    private fun f3(a: org.json.JSONArray) = floatArrayOf(a.getDouble(0).toFloat(), a.getDouble(1).toFloat(), a.getDouble(2).toFloat())

    /** Call once per rendered frame. Messages arrive at ~20/s, the screen runs at 60 fps -> ease toward the target. */
    fun update(s: SteeringSignal, smoothing: Float = 0.35f) {
        for (b in bindings.values) {
            val v = when (b.source) {
                "steeringWheelDeg" -> s.steeringWheelDeg
                "leftWheelDeg" -> s.leftWheelDeg
                "rightWheelDeg" -> s.rightWheelDeg
                else -> (s.leftWheelDeg + s.rightWheelDeg) / 2f
            }
            val target = (b.sign * v + b.offsetDeg).coerceIn(b.minDeg, b.maxDeg)
            b.currentDeg += (target - b.currentDeg) * smoothing
        }
    }

    /** Matrix to PRE-multiply onto each node's rest world matrix: delta = T(p) R(axis,deg) T(-p). */
    fun deltaMatrix(key: String): FloatArray {
        val b = bindings.getValue(key)
        val r = FloatArray(16); val t1 = FloatArray(16); val t2 = FloatArray(16); val tmp = FloatArray(16); val out = FloatArray(16)
        Matrix.setRotateM(r, 0, b.currentDeg, b.axis[0], b.axis[1], b.axis[2])   // degrees, right-hand rule
        Matrix.setIdentityM(t1, 0); Matrix.translateM(t1, 0, b.pivot[0], b.pivot[1], b.pivot[2])
        Matrix.setIdentityM(t2, 0); Matrix.translateM(t2, 0, -b.pivot[0], -b.pivot[1], -b.pivot[2])
        Matrix.multiplyMM(tmp, 0, r, 0, t2, 0)       // R * T(-p)
        Matrix.multiplyMM(out, 0, t1, 0, tmp, 0)     // T(p) * R * T(-p)
        return out
    }

    /** newWorld = delta * restWorld   (apply to every node name in bindings[key].nodes) */
    fun apply(key: String, restWorld: FloatArray): FloatArray {
        val out = FloatArray(16); Matrix.multiplyMM(out, 0, deltaMatrix(key), 0, restWorld, 0); return out
    }
}
