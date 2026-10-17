package com.middleware.panorama.wp5;

import org.opencv.core.Mat;
import org.opencv.core.MatOfByte;
import org.opencv.core.MatOfInt;
import org.opencv.imgcodecs.Imgcodecs;

import java.io.ByteArrayOutputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Bridge to the Python "vision" sidecar (vision/service.py, a segmentation network that finds curbs).
 *
 *   Java  --(JPEG of every straightened camera frame, ~10 Hz)-->  POST  http://127.0.0.1:8765/frame        (this class, background thread)
 *   Java  <--(curb found / not found per camera + contour)-----  POST  /api/v1/vision/detections          (Wp5Service handler -> ingest())
 *
 * Which engine makes the curb zones is chosen with   -Dwp5.vision.engine=classic | sidecar | auto
 *   classic  (DEFAULT) the built-in edge detector only. This class does NOTHING (no thread, no network). Same behaviour as before.
 *   sidecar  only the Python network makes zones. If it is not running there are simply no zones.
 *   auto     the Python network when it is running (results arrive), otherwise the built-in detector.
 *
 * Safety: nothing here can stop or slow the video. offer() only copies a picture into a slot (and does not even do that while the
 * sidecar is unreachable); sending, encoding and all network errors live on one daemon thread and are caught.
 */
public final class VisionSidecar {

    public enum Engine { CLASSIC, SIDECAR, AUTO }

    public static final Engine ENGINE = parse(System.getProperty("wp5.vision.engine", "classic"));
    private static final String BASE = System.getProperty("wp5.vision.sidecar.url", "http://127.0.0.1:8765").replaceAll("/+$", "");
    private static final long ALIVE_MS = Long.getLong("wp5.vision.sidecar.aliveMs", 3000L);
    private static final long RETRY_MS = 5000L;
    private static final int JPEG_QUALITY = Integer.getInteger("wp5.vision.sidecar.jpegQuality", 85);

    private static final VisionSidecar INSTANCE = new VisionSidecar();
    public static VisionSidecar get() { return INSTANCE; }

    private final ConcurrentHashMap<String, AtomicReference<Mat>> slots = new ConcurrentHashMap<>();
    private final AtomicLong frameId = new AtomicLong();
    private final AtomicLong sent = new AtomicLong(), failed = new AtomicLong(), received = new AtomicLong();
    private volatile long backoffUntilMs = 0;
    private volatile long lastResultMs = 0;
    private final long[] recent = new long[20];            // arrival times of the last results -> results per second
    private int recentN = 0;
    /** last metric (calibrated) distance per camera {metres, timestampMs}; bridges the single frames the sidecar rejects as "duplicate timestamp" */
    private final ConcurrentHashMap<String, double[]> lastMetric = new ConcurrentHashMap<>();
    private static final long METRIC_HOLD_MS = Long.getLong("wp5.vision.sidecar.metricHoldMs", 1000L);
    private volatile boolean reachable = true;       // only used to log state changes once
    /** raw result message of the sidecar (full contours in panorama pixels) - served by GET /api/v1/vision/detections for the pink overlay */
    private volatile String latestJson = "";
    private Thread worker;

    private VisionSidecar() { }

    private static Engine parse(String s) {
        try { return Engine.valueOf(s.trim().toUpperCase(Locale.ROOT)); }
        catch (Exception e) { System.err.println("[VISION] unknown -Dwp5.vision.engine=" + s + " -> using classic"); return Engine.CLASSIC; }
    }

    /** True when the Python sidecar is used at all (engine sidecar or auto). */
    public static boolean enabled() { return ENGINE != Engine.CLASSIC; }

    /** The sidecar answered recently (it posts a result message about 10 times per second while it receives frames). */
    public boolean alive() { return lastResultMs > 0 && System.currentTimeMillis() - lastResultMs < ALIVE_MS; }

    /** May the built-in edge detector create zones right now? (so two detectors never fight over the same side) */
    public static boolean classicAllowed() {
        switch (ENGINE) {
            case SIDECAR: return false;
            case AUTO:    return !INSTANCE.alive();
            default:      return true;
        }
    }

    // ------------------------------------------------------------------ Java -> sidecar (frames)

    /** Called by the video loop. Never blocks, never throws. The picture is copied, the caller may reuse its Mat. */
    public void offer(String camera, Mat bgr) {
        try {
            if (!enabled() || camera == null || bgr == null || bgr.empty()) return;
            if (System.currentTimeMillis() < backoffUntilMs) return;             // sidecar is down: do not even copy
            ensureWorker();
            Mat old = slots.computeIfAbsent(camera, k -> new AtomicReference<>()).getAndSet(bgr.clone());
            if (old != null) old.release();                                       // only the newest picture per camera is kept
        } catch (Throwable t) {
            System.err.println("[VISION] offer: " + t);
        }
    }

    private synchronized void ensureWorker() {
        if (worker != null) return;
        worker = new Thread(this::sendLoop, "wp5-vision-sidecar-sender");
        worker.setDaemon(true);
        worker.start();
        System.out.println("[VISION] sending camera frames to the vision sidecar at " + BASE + "/frame   (engine=" + ENGINE.name().toLowerCase(Locale.ROOT) + ")");
    }

    private void sendLoop() {
        while (!Thread.currentThread().isInterrupted()) {
            boolean didWork = false;
            for (Map.Entry<String, AtomicReference<Mat>> e : slots.entrySet()) {
                Mat m = e.getValue().getAndSet(null);
                if (m == null) continue;
                didWork = true;
                try {
                    if (System.currentTimeMillis() < backoffUntilMs) continue;    // finally{} releases m
                    postFrame(e.getKey(), m);
                    sent.incrementAndGet();
                    if (!reachable) { reachable = true; System.out.println("[VISION] vision sidecar connected"); }
                } catch (Throwable t) {
                    failed.incrementAndGet();
                    backoffUntilMs = System.currentTimeMillis() + RETRY_MS;
                    if (reachable) {
                        reachable = false;
                        System.err.println("[VISION] vision sidecar not reachable at " + BASE + " (" + t.getClass().getSimpleName()
                                + ": " + t.getMessage() + ") - will retry every " + (RETRY_MS / 1000) + " s. "
                                + (ENGINE == Engine.AUTO ? "Built-in detector is used meanwhile." : "No zones until it is running."));
                    }
                } finally {
                    m.release();
                }
            }
            if (!didWork) { try { Thread.sleep(8); } catch (InterruptedException ie) { return; } }
        }
    }

    private void postFrame(String camera, Mat bgr) throws Exception {
        MatOfByte jpg = new MatOfByte();
        try {
            if (!Imgcodecs.imencode(".jpg", bgr, jpg, new MatOfInt(Imgcodecs.IMWRITE_JPEG_QUALITY, JPEG_QUALITY)))
                throw new IllegalStateException("jpeg encode failed");
            String b = "----wp5vision" + Long.toHexString(System.nanoTime());
            ByteArrayOutputStream body = new ByteArrayOutputStream(jpg.rows() + 1024);
            field(body, b, "camera", camera);
            field(body, b, "frameId", Long.toString(frameId.incrementAndGet()));
            field(body, b, "timestampMs", Long.toString(System.currentTimeMillis()));
            field(body, b, "width", Integer.toString(bgr.cols()));
            field(body, b, "height", Integer.toString(bgr.rows()));
            body.write(("--" + b + "\r\nContent-Disposition: form-data; name=\"image\"; filename=\"f.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n")
                    .getBytes(StandardCharsets.US_ASCII));
            body.write(jpg.toArray());
            body.write(("\r\n--" + b + "--\r\n").getBytes(StandardCharsets.US_ASCII));

            HttpURLConnection c = (HttpURLConnection) java.net.URI.create(BASE + "/frame").toURL().openConnection();
            c.setConnectTimeout(400);
            c.setReadTimeout(2000);
            c.setDoOutput(true);
            c.setRequestMethod("POST");
            c.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + b);
            c.setFixedLengthStreamingMode(body.size());
            try (OutputStream os = c.getOutputStream()) { body.writeTo(os); }
            int code = c.getResponseCode();
            try (java.io.InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream()) { if (in != null) in.readAllBytes(); }
            c.disconnect();
            if (code != 200) throw new IllegalStateException("sidecar answered HTTP " + code);
        } finally {
            jpg.release();
        }
    }

    private static void field(ByteArrayOutputStream o, String boundary, String name, String value) throws java.io.IOException {
        o.write(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name + "\"\r\n\r\n" + value + "\r\n").getBytes(StandardCharsets.UTF_8));
    }

    // ------------------------------------------------------------------ sidecar -> Java (results)

    /**
     * Result message of the sidecar (body of POST /api/v1/vision/detections). For every camera it says DETECTED (with a contour in
     * CAMERA pixels) or NO_CURB / STALE. DETECTED -> curb zone on that side; everything else -> that side's zone is cleared.
     * Distance: the sidecar's metric distance only when its calibration is valid, otherwise the same row->metres placeholder
     * calibration the built-in detector uses (CurbVision.rowToMetres: CALIBRATE it on the real vehicle).
     *
     * @return number of cameras that were updated (0 = message not understood)
     */
    @SuppressWarnings("unchecked")
    int ingest(String body, CurbModel model, long nowMs) {
        if (ENGINE == Engine.CLASSIC) return 0;                                 // classic mode ignores the sidecar completely
        Object root = MiniJson.parse(body);
        if (!(root instanceof Map)) throw new IllegalArgumentException("not a JSON object");
        Map<String, Object> msg = (Map<String, Object>) root;
        lastResultMs = nowMs;
        latestJson = body;
        received.incrementAndGet();
        synchronized (recent) { recent[recentN++ % recent.length] = nowMs; }

        int updated = 0;
        java.util.Set<String> done = new java.util.HashSet<>();
        Object dets = msg.get("detections");
        if (dets instanceof List) {
            for (Object o : (List<Object>) dets) {
                if (!(o instanceof Map)) continue;
                Map<String, Object> d = (Map<String, Object>) o;
                String cam = str(d.get("camera")).toLowerCase(Locale.ROOT);
                if (cam.isEmpty() || model.cameraIndex(cam) < 0) continue;
                if (!Boolean.TRUE.equals(d.get("detectionValid")) || !"DETECTED".equals(str(d.get("overlayStatus")))) continue;
                List<double[]> pts = points(d.get("contourCameraPixels"));
                if (pts.size() < 2) continue;
                double meanY = 0;
                for (double[] p : pts) meanY += p[1];
                meanY /= pts.size();
                int h = (int) num(d.get("imageHeight"), 360);
                double conf = Math.max(0, Math.min(1, num(d.get("confidence"), 0.5)));
                double dist;
                if (Boolean.TRUE.equals(d.get("distanceValid")) && d.get("distanceM") != null) {
                    dist = num(d.get("distanceM"), 1.0);                          // metric distance from the sidecar (needs VALIDATED calibration)
                    lastMetric.put(cam, new double[]{dist, nowMs});
                } else {
                    double[] lm = lastMetric.get(cam);
                    dist = (lm != null && nowMs - lm[1] <= METRIC_HOLD_MS)       // metric was valid a moment ago: keep it (never mix two scales)
                            ? lm[0] : CurbVision.rowToMetres(meanY, h, cam);       // otherwise the placeholder row->metres scale
                }
                double[] a = pts.get(0), z = pts.get(pts.size() - 1);
                model.ingestVision(cam, true, dist, conf, new double[]{a[0], a[1], z[0], z[1]}, cam, "CAMERA", nowMs);
                done.add(cam);
                updated++;
            }
        }
        Object st = msg.get("cameraOverlayStatus");
        if (st instanceof Map) {
            for (Map.Entry<String, Object> e : ((Map<String, Object>) st).entrySet()) {
                String cam = e.getKey().toLowerCase(Locale.ROOT);
                if (done.contains(cam) || model.cameraIndex(cam) < 0) continue;
                model.ingestVision(cam, false, 0, 0, null, cam, "CAMERA", nowMs);   // no curb / stale / error -> clear that side
                lastMetric.remove(cam);
                updated++;
            }
        }
        return updated;
    }

    /** Last message the sidecar posted ("" if none yet). Used by the viewer to draw the full curb contour. */
    public String latestJson() { return latestJson; }

    /** JSON for GET /api/v1/vision/status (is the sidecar connected? how many frames / results?). */
    String statusJson() {
        long now = System.currentTimeMillis();
        return "{\"engine\":\"" + ENGINE.name().toLowerCase(Locale.ROOT) + "\",\"sidecarUrl\":\"" + BASE + "\",\"sidecarAlive\":" + alive()
                + ",\"lastResultAgeMs\":" + (lastResultMs == 0 ? -1 : now - lastResultMs)
                + ",\"resultsPerSecond\":" + String.format(Locale.ROOT, "%.1f", resultsPerSecond())
                + ",\"framesSent\":" + sent.get() + ",\"sendFailures\":" + failed.get() + ",\"resultsReceived\":" + received.get()
                + ",\"builtInDetectorActive\":" + classicAllowed() + "}";
    }

    /** How fast the sidecar answers (about 10 is ideal; below ~2 the zone timer visionStaleMs must be raised). */
    private double resultsPerSecond() {
        synchronized (recent) {
            int n = Math.min(recentN, recent.length);
            if (n < 2) return 0;
            long newest = 0, oldest = Long.MAX_VALUE;
            for (int i = 0; i < n; i++) { newest = Math.max(newest, recent[i]); oldest = Math.min(oldest, recent[i]); }
            long age = System.currentTimeMillis() - newest;
            return age > 3000 || newest == oldest ? 0 : (n - 1) * 1000.0 / (newest - oldest);
        }
    }

    // ------------------------------------------------------------------ tiny helpers

    private static String str(Object o) { return o == null ? "" : o.toString(); }
    private static double num(Object o, double dflt) { return o instanceof Number ? ((Number) o).doubleValue() : dflt; }

    @SuppressWarnings("unchecked")
    private static List<double[]> points(Object o) {
        List<double[]> out = new java.util.ArrayList<>();
        if (!(o instanceof List)) return out;
        for (Object p : (List<Object>) o) {
            if (p instanceof List && ((List<Object>) p).size() >= 2) {
                List<Object> q = (List<Object>) p;
                out.add(new double[]{num(q.get(0), 0), num(q.get(1), 0)});
            }
        }
        return out;
    }
}
