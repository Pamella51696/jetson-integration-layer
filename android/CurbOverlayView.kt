package com.example.seethrough.wp5

import android.content.Context
import android.graphics.*
import android.util.AttributeSet
import android.view.View

/**
 * Transparent view laid EXACTLY over the panorama view (same size, e.g. both match_parent in a FrameLayout).
 * Zones are drawn for ALL sides (front, rear, left, right).
 * Each side that has a curb in its camera frame gets 3 DOTTED zone lines in that camera strip, like a reversing-camera guide:
 *   bottom = DANGER (red), middle = WARNING (yellow), top = SAFE (green).
 * The line of the current zone is bold, the other two are faded. No filled blocks.
 * Usage:  curbOverlay.signal = latestCurbSignal   (call from the UI thread, e.g. in a flow collector)
 */
class CurbOverlayView @JvmOverloads constructor(c: Context, a: AttributeSet? = null) : View(c, a) {

    var signal: CurbSignal? = null
        set(v) { field = v; postInvalidateOnAnimation() }

    /** Zones are NOT drawn on the camera frames by default; set true to show them on the panorama again. */
    var showOnPanorama = false

    private val dp = resources.displayMetrics.density
    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND
        pathEffect = DashPathEffect(floatArrayOf(9f * dp, 7f * dp), 0f)
    }
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE; textAlign = Paint.Align.CENTER; textSize = 12f * dp
        isFakeBoldText = true; setShadowLayer(4f, 0f, 0f, Color.BLACK)
    }
    // index 0 = DANGER (near/bottom), 1 = WARNING, 2 = SAFE (far/top)
    private val zoneColors = intArrayOf(Color.parseColor("#FF3B30"), Color.parseColor("#FFCC00"), Color.parseColor("#34C759"))
    private val grey = Color.parseColor("#9E9E9E")
    private val path = Path()

    private fun isCurb(z: CurbZone) = true   // every zone is a curb found in a camera frame (LEFT / RIGHT / FRONT / REAR)

    override fun onDraw(canvas: Canvas) {
        if (!showOnPanorama) return
        val s = signal ?: return
        val w = width.toFloat(); val h = height.toFloat()
        val yB = h * 0.90f; val yT = h * 0.42f; val inB = 0.03f; val inT = 0.22f
        for (z in s.zones) {
            if (!isCurb(z) || z.wNorm <= 0f) continue
            val x = z.xNorm * w; val zw = z.wNorm * w
            val none = z.zone == Zone.NONE
            val active = when (z.zone) { Zone.DANGER -> 0; Zone.WARNING -> 1; Zone.SAFE -> 2; else -> -1 }
            fun xl(t: Float) = x + zw * (inB + (inT - inB) * t)
            fun xr(t: Float) = x + zw * (1f - (inB + (inT - inB) * t))

            // side edges, coloured like the current zone
            line.color = if (none) grey else z.colorArgb; line.alpha = 230; line.strokeWidth = 2f * dp
            path.reset(); path.moveTo(xl(0f), yB); path.lineTo(xl(1f), yT)
            path.moveTo(xr(0f), yB); path.lineTo(xr(1f), yT); canvas.drawPath(path, line)

            // the 3 zone lines
            for (i in 0..2) {
                val t = i / 2f; val y = yB + (yT - yB) * t
                line.color = if (none) grey else zoneColors[i]
                line.alpha = if (i == active) 255 else 140
                line.strokeWidth = (if (i == active) 4f else 2.5f) * dp
                path.reset(); path.moveTo(xl(t), y); path.lineTo(xr(t), y); canvas.drawPath(path, line)
            }
            val label = z.distanceM?.let { "%.2f m".format(it) } ?: z.zone.name
            canvas.drawText("${z.pos.replace('_', ' ')}  $label", x + zw / 2, yB + 16f * dp, text)
        }
    }
}
