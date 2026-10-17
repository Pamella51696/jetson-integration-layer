package com.middleware.panorama.wp5;

import com.middleware.panorama.StitchingCore;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * WP-5 / Package 2 - curb zones, measured ONLY from the camera frames (no distance sensors).
 *
 * Input : per side (LEFT / RIGHT / FRONT / REAR) the result of the frame analysis (CurbVision):
 *         "is there a curb in this camera's frame, and how far from the car is it?"
 * Output: per side with a curb: RED / YELLOW / GREEN zone (with hysteresis)
 *         + where that zone sits on the stitched panorama (pixel and 0..1 coordinates)
 *         + left / right / front / rear / nearest summary for "context-aware emphasis" on Android.
 *
 * A side WITHOUT a curb in its frame produces NO zone at all (nothing wrong is displayed).
 */
public final class CurbModel {

    /** The four sides. The camera of a side must be named exactly like the side, in lower case. */
    public static final String[] SIDES = {"LEFT", "RIGHT", "FRONT", "REAR"};

    /** -Dwp5.allowTest=false : ignore hand-typed TEST input from the viewer, so a zone can ONLY come from the camera frames. */
    private static final boolean ALLOW_TEST = !"false".equalsIgnoreCase(System.getProperty("wp5.allowTest", "true"));
    /** -Dwp5.trace=false : switch off the console line that is printed whenever a side's curb zone appears / disappears. */
    private static final boolean TRACE = !"false".equalsIgnoreCase(System.getProperty("wp5.trace", "true"));

    private static final class Side {
        boolean detected;
        double confidence, distanceM;
        String source = "CAMERA";
        long receivedMs;
        double[] linePano;                 // x1,y1,x2,y2 in PANORAMA px (null if the detector gave no line)
        Zone zone = Zone.NONE;             // remembered for hysteresis
    }

    /** Result of one evaluation. */
    public static final class CurbState {
        public final String json;
        public final String signature; // changes only when something visible changed
        public final Zone worst;
        CurbState(String json, String signature, Zone worst) {
            this.json = json; this.signature = signature; this.worst = worst;
        }
    }

    private final Wp5Config cfg;
    private final String[] cameraNames;
    private final int camW, camH, overlap;
    private final Map<String, Side> sides = new LinkedHashMap<>();
    private boolean started;                                              // set by the first result; after that we keep publishing (so "curb gone" reaches the apps)
    private final Map<String, Long> testLockUntil = new LinkedHashMap<>();   // TEST input beats the detector for 1.5 s

    public CurbModel(Wp5Config cfg, String[] cameraNames, int camW, int camH, int overlapPx) {
        this.cfg = cfg; this.cameraNames = cameraNames;
        this.camW = camW; this.camH = camH;
        this.overlap = StitchingCore.clampOverlap(overlapPx, camW);
    }

    // ------------------------------------------------------------------ input

    /**
     * Result of the frame analysis for one side ("left" / "right" / "front" / "rear").
     * detected=false -> NO zone is shown for that side.
     * detected=true  -> a curb at distanceM metres from the car on that side.
     * linePx (optional) = curb line in camera-frame pixels of cameraName; converted to panorama pixels here.
     * source "TEST" = typed in by hand (viewer): it overrides the detector for 1.5 s.
     */
    public synchronized void ingestVision(String side, boolean detected, double distanceM, double confidence,
                                          double[] linePx, String cameraName, String source, long nowMs) {
        String S = side.toUpperCase(Locale.ROOT);
        if (!isSide(S)) return;
        boolean test = "TEST".equals(source);
        if (test && !ALLOW_TEST) return;                                // frames only
        if (test) testLockUntil.put(S, nowMs + 1500);
        else if (nowMs < testLockUntil.getOrDefault(S, 0L)) return;     // hand-made test input is active

        started = true;
        Side v = sides.computeIfAbsent(S, k -> new Side());
        if (TRACE && v.detected != detected)                            // one line per change: WHAT made the zone and WHERE it came from
            System.out.println("[CURBMODEL] " + S + (detected ? " curb ON  " : " curb OFF ") + "source=" + (test ? "TEST (typed by hand)" : "CAMERA FRAME")
                    + (detected ? String.format(Locale.ROOT, " distance=%.2fm confidence=%.2f line(camera px)=%s", distanceM, confidence,
                        linePx == null ? "-" : String.format(Locale.ROOT, "(%.0f,%.0f)-(%.0f,%.0f)", linePx[0], linePx[1], linePx[2], linePx[3])) : ""));
        v.detected = detected; v.confidence = confidence; v.distanceM = distanceM;
        v.source = test ? "TEST" : "CAMERA"; v.receivedMs = nowMs;
        v.linePano = null;
        if (detected && linePx != null && cameraName != null) {
            int ci = indexOf(cameraName);
            if (ci >= 0) {
                int off = StitchingCore.cameraX(ci, camW, overlap);
                v.linePano = new double[]{off + linePx[0], linePx[1], off + linePx[2], linePx[3]};
            }
        }
    }

    private static final Pattern OBJ = Pattern.compile("\\{[^{}]*\\}");

    /** Parses {"sides":[{"side":"left","detected":true,"distanceM":0.7,"confidence":0.9}, ...]}  (TEST input from the viewer). */
    public synchronized int ingestVisionJson(String body, long nowMs) {
        int ok = 0;
        Matcher m = OBJ.matcher(body.substring(Math.max(0, body.indexOf('['))));
        while (m.find()) {
            String o = m.group();
            String side = Json.getStr(o, "side", "");
            if (!isSide(side.toUpperCase(Locale.ROOT))) continue;
            ingestVision(side, Json.getBool(o, "detected", false), Json.getNum(o, "distanceM", 1.0),
                         Json.getNum(o, "confidence", 1.0), null, null, "TEST", nowMs);
            ok++;
        }
        return ok;
    }

    private static boolean isSide(String s) {
        for (String x : SIDES) if (x.equals(s)) return true;
        return false;
    }

    // ------------------------------------------------------------ classification

    /**
     * Pure function with hysteresis. Getting WORSE happens immediately at the threshold;
     * getting BETTER needs an extra hysteresisM so the colour does not flicker.
     */
    Zone classify(double d, Zone previous) {
        if (d < 0 || d >= cfg.maxRangeM) return Zone.SAFE;          // nothing close
        Zone raw = d <= cfg.dangerM ? Zone.DANGER : (d <= cfg.warningM ? Zone.WARNING : Zone.SAFE);
        if (previous == Zone.NONE || raw.level >= previous.level) return raw;
        // improving: require margin beyond the threshold we are leaving
        double leaving = previous == Zone.DANGER ? cfg.dangerM : cfg.warningM;
        return d > leaving + cfg.hysteresisM ? raw : previous;
    }

    // ------------------------------------------------------------------ output

    public synchronized CurbState snapshot(long seq, long nowMs) {
        int n = cameraNames.length;
        int panoW = StitchingCore.panoramaWidth(n, camW, overlap);

        // a result nobody refreshes simply disappears (no grey leftovers)
        sides.values().removeIf(x -> nowMs - x.receivedMs > cfg.visionStaleMs);

        StringBuilder sb = new StringBuilder(1024);
        StringBuilder sig = new StringBuilder();
        Zone worst = Zone.NONE;
        Map<String, Zone> perSide = new LinkedHashMap<>();
        for (String S : SIDES) perSide.put(S, Zone.NONE);
        double nearest = Double.MAX_VALUE; String nearestId = null;

        sb.append("{\"type\":\"curb\",\"v\":2,\"seq\":").append(seq).append(",\"ts\":").append(nowMs)
          .append(",\"panorama\":{\"width\":").append(panoW).append(",\"height\":").append(camH).append('}')
          .append(",\"thresholds\":{\"dangerM\":").append(Json.num(cfg.dangerM))
          .append(",\"warningM\":").append(Json.num(cfg.warningM)).append('}')
          .append(",\"zones\":[");

        boolean first = true;
        for (String S : SIDES) {
            Side v = sides.get(S);
            if (v == null || !v.detected) continue;                       // no curb in this frame -> no zone
            Zone z = classify(v.distanceM, v.zone);
            v.zone = z;

            String cam = S.toLowerCase(Locale.ROOT);
            int camIdx = indexOf(cam);
            int x = 0, w = 0;
            if (camIdx >= 0) {                                            // the zone covers the whole strip of that camera
                x = StitchingCore.cameraX(camIdx, camW, overlap);
                w = camW;
                if (x + w > panoW) w = panoW - x;
            }
            String id = "CURB_" + S;
            if (!first) sb.append(','); first = false;
            sb.append("{\"id\":").append(Json.str(id))
              .append(",\"pos\":").append(Json.str(S))
              .append(",\"kind\":\"CURB\"")
              .append(",\"source\":").append(Json.str(v.source))
              .append(",\"confidence\":").append(Json.num(v.confidence))
              .append(",\"distanceM\":").append(Json.num(v.distanceM))
              .append(",\"zone\":").append(Json.str(z.name()))
              .append(",\"level\":").append(z.level)
              .append(",\"color\":").append(Json.str(cfg.color(z)))
              .append(",\"camera\":").append(Json.str(cam))
              .append(",\"pano\":{\"x\":").append(x).append(",\"w\":").append(w)
              .append(",\"xNorm\":").append(Json.num(panoW == 0 ? 0 : (double) x / panoW))
              .append(",\"wNorm\":").append(Json.num(panoW == 0 ? 0 : (double) w / panoW)).append("}}");

            sig.append(id).append(':').append(z.level).append(':').append(Math.round(v.distanceM * 50)).append(';'); // 2 cm steps
            worst = Zone.worst(worst, z);
            perSide.put(S, z);
            if (v.distanceM < nearest) { nearest = v.distanceM; nearestId = id; }
        }
        sb.append("],\"summary\":{\"worstZone\":").append(Json.str(worst.name()))
          .append(",\"worstColor\":").append(Json.str(cfg.color(worst)))
          .append(",\"nearestDistanceM\":").append(nearestId == null ? "null" : Json.num(nearest))
          .append(",\"nearestZoneId\":").append(nearestId == null ? "null" : Json.str(nearestId));
        for (String S : SIDES)
            sb.append(",\"").append(S.toLowerCase(Locale.ROOT)).append("Worst\":").append(Json.str(perSide.get(S).name()));
        sb.append("},\"vision\":{\"mode\":\"camera\"");
        for (String S : SIDES) {
            Side v = sides.get(S);
            sb.append(",\"").append(S.toLowerCase(Locale.ROOT)).append("\":");
            if (v == null || !v.detected) { sb.append("{\"detected\":false}"); sig.append(S).append(":n;"); continue; }
            sb.append("{\"detected\":true")
              .append(",\"confidence\":").append(Json.num(v.confidence))
              .append(",\"distanceM\":").append(Json.num(v.distanceM))
              .append(",\"source\":").append(Json.str(v.source))
              .append(",\"line\":");
            if (v.linePano == null) sb.append("null");
            else sb.append("{\"x1\":").append(Math.round(v.linePano[0])).append(",\"y1\":").append(Math.round(v.linePano[1]))
                   .append(",\"x2\":").append(Math.round(v.linePano[2])).append(",\"y2\":").append(Math.round(v.linePano[3])).append('}');
            sb.append('}');
        }
        sb.append("}}");
        return new CurbState(sb.toString(), sig.toString(), worst);
    }

    private int indexOf(String cam) {
        for (int i = 0; i < cameraNames.length; i++) if (cameraNames[i].equals(cam)) return i;
        return -1;
    }

    /** true once the first frame result has arrived (even "no curb"), so the stream starts publishing. */
    public synchronized boolean hasData() { return started; }

    public int cameraIndex(String name) { return indexOf(name); }
}
