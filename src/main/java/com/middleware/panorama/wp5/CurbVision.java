package com.middleware.panorama.wp5;

import org.opencv.core.Core;
import org.opencv.core.Mat;
import org.opencv.core.MatOfDouble;
import org.opencv.core.Rect;
import org.opencv.core.Point;
import org.opencv.core.Scalar;
import org.opencv.core.Size;
import org.opencv.imgcodecs.Imgcodecs;
import org.opencv.imgproc.Imgproc;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/**
 * Curb detection from the CONTENT of a camera frame (baseline, classic OpenCV).
 *
 * Input : one straightened camera frame (640x360 BGR) of ANY of the four cameras (left, right, front, rear).
 * Output: Result { detected, confidence, distanceM, line }.
 *
 * What is called a "curb" here: two long, parallel edges close together (the curb face) at ANY slant up to
 * maxAngleDeg (flat, or running away diagonally in perspective). Painted (e.g. yellow) curbs count too. If several
 * pairs are found in a frame, the one closest to the car wins. If nothing like that is in the frame:
 * detected = false, and nothing is shown - no wrong zones.
 *
 * Side cameras (left/right) see a curb that runs ALONG the car; front/rear cameras see a curb that lies
 * ACROSS the driving direction (kerb ahead / behind, parking against a kerb). Both show up as a near-horizontal
 * band in the straightened frame, so one detector serves all four cameras - only the calibration differs.
 *
 * Distance: the image row of the curb's lower edge is mapped linearly to metres (rowAt0m ... rowAtFar).
 * THE DEFAULTS ARE PLACEHOLDERS - calibrate each camera with your real footage.
 *
 * All tunables are system properties. A value can be set for ONE camera or for all of them:
 *   -Dwp5.vision.front.rowAt0m=300      (only the front camera)
 *   -Dwp5.vision.minScore=0.6           (all cameras)
 * Per-camera value wins over the general value, which wins over the built-in default.
 */
public final class CurbVision {
    private CurbVision() {}

    /**
     * Settings per camera. Order of lookup: -Dwp5.vision.<camera>.<key>  ->  -Dwp5.vision.<key>  ->  CAMERA_DEFAULTS  ->  general default.
     * CAMERA_DEFAULTS were calibrated on the 4 test clips (front / right / rear / left):
     *   - the car's own body (bumper, number plate, mudguard) fills the bottom of the right/rear frames -> roiBottom 0.80 keeps it out;
     *   - a curb that is seen far away (the concrete kerb in the right clip) is still a curb -> minNearRow 0 (far = green zone, not "no curb");
     *   - the right camera needs weaker edges and shorter lines (the kerb edges are low-contrast); the rear camera needs longer lines,
     *     otherwise the edge of the parked taxi is taken for a curb.
     */
    private static final java.util.Map<String, double[]> CAMERA_DEFAULTS = new java.util.HashMap<>();
    private static final String[] CAMERA_KEYS = {"roiTop", "roiBottom", "minLenFrac", "minNearRow", "cannyLow", "cannyHigh"};
    static {
        //                                 roiTop roiBot minLen nearRow canLo canHi
        // RIGHT: no special row any more. The old row (roiTop 0.00 / minNearRow 0 / minLen 0.22 / canny 20-60) made the detector search the
        // whole picture incl. the far background and report ANY pair of long edges there (buildings, far pavement, cars) as a "curb"
        // -> a wrong GREEN zone on the right although no curb is near the car, while the real kerb next to the car was missed.
        // Right now uses the general defaults (look at the lower 70 %, the curb must reach the lower 40 % of the picture).
        CAMERA_DEFAULTS.put("rear",  new double[]{0.30, 0.80, 0.28, 0.00, 20, 60});
    }

    static double p(String cam, String k, double d) {
        String v = cam == null ? null : System.getProperty("wp5.vision." + cam + "." + k);
        if (v == null) v = System.getProperty("wp5.vision." + k);
        if (v != null) return Double.parseDouble(v.trim());
        double[] cd = cam == null ? null : CAMERA_DEFAULTS.get(cam);
        if (cd != null) for (int i = 0; i < CAMERA_KEYS.length; i++) if (CAMERA_KEYS[i].equals(k)) return cd[i];
        return d;
    }

    /** Front / rear cameras: the car's own bumper fills the lower part of the frame, so look higher up; kerb is across the view. */
    private static boolean endCamera(String cam) { return "front".equals(cam) || "rear".equals(cam); }

    /** Final answer for one side (smoothed over several frames). */
    public static final class Result {
        public final boolean detected;
        public final double confidence;     // 0..1
        public final double distanceM;      // metres from the car side to the curb (only if detected)
        public final double[] line;         // x1,y1,x2,y2 in camera-frame pixels (only if detected), else null
        Result(boolean d, double c, double dist, double[] l) { detected = d; confidence = c; distanceM = dist; line = l; }
    }

    private static final class Seg {
        double x1, y1, x2, y2, len, ang;
        double yAt(double x) { return x2 == x1 ? y1 : y1 + (y2 - y1) * (x - x1) / (x2 - x1); }
    }

    static final class Raw { double score, rowBase, ang, sep, ov; double[] line; }

    // ------------------------------------------------------------------ one frame

    static Raw detectRaw(Mat bgr, String cam) {
        if (bgr == null || bgr.empty() || bgr.channels() != 3) return null;
        final int W = bgr.cols(), H = bgr.rows();
        final int roiTop = (int) (H * p(cam, "roiTop", 0.30)), roiBot = (int) (H * p(cam, "roiBottom", 0.97));
        final double minLen = W * p(cam, "minLenFrac", 0.12), maxAng = p(cam, "maxAngleDeg", 65), parTol = p(cam, "parallelDeg", 7);
        final double minSep = p(cam, "minSepPx", 4), maxSep = p(cam, "maxSepPx", 40);
        final double minScore = p(cam, "minScore", 0.68);

        Mat gray = new Mat(), hsv = new Mat(), blur = new Mat(), edges = new Mat(), lines = new Mat();
        try {
            Imgproc.cvtColor(bgr, gray, Imgproc.COLOR_BGR2GRAY);
            Imgproc.cvtColor(bgr, hsv, Imgproc.COLOR_BGR2HSV);
            Imgproc.GaussianBlur(gray.rowRange(roiTop, roiBot), blur, new Size(5, 5), 0);
            Imgproc.Canny(blur, edges, p(cam, "cannyLow", 30), p(cam, "cannyHigh", 90));
            Imgproc.HoughLinesP(edges, lines, 1, Math.PI / 180, (int) p(cam, "houghThreshold", 30), minLen, p(cam, "maxGapPx", 15));

            List<Seg> segs = new ArrayList<>();
            for (int i = 0; i < lines.rows(); i++) {
                double[] v = lines.get(i, 0);
                Seg s = new Seg();
                if (v[0] <= v[2]) { s.x1 = v[0]; s.y1 = v[1] + roiTop; s.x2 = v[2]; s.y2 = v[3] + roiTop; }
                else              { s.x1 = v[2]; s.y1 = v[3] + roiTop; s.x2 = v[0]; s.y2 = v[1] + roiTop; }
                s.len = Math.hypot(s.x2 - s.x1, s.y2 - s.y1);
                s.ang = Math.toDegrees(Math.atan2(s.y2 - s.y1, s.x2 - s.x1));
                int[] sm = SEAMS.get();                                    // blended seam between two cameras is not a curb
                if (sm != null && (s.x2 <= sm[0] || s.x1 >= W - sm[1])) continue;
                if (Math.abs(s.ang) <= maxAng && s.len >= minLen) segs.add(s);
            }
            segs.sort(Comparator.comparingDouble((Seg s) -> -s.len));
            if (segs.size() > 60) segs = segs.subList(0, 60);

            // A curb = two long, parallel edges close together (the curb face) at ANY slant: along the car (flat),
            // across the view (flat) or running away in perspective (diagonal, up to maxAngleDeg).
            // Every such pair is a candidate; the one NEAREST to the car (and strong) wins.
            Raw best = null; double bestRank = -1;
            for (int i = 0; i < segs.size(); i++) {
                for (int j = i + 1; j < segs.size(); j++) {
                    Seg a = segs.get(i), b = segs.get(j);
                    double dAng = Math.abs(a.ang - b.ang);
                    if (dAng > parTol) continue;
                    // overlap measured along a's direction
                    double ux = Math.cos(Math.toRadians(a.ang)), uy = Math.sin(Math.toRadians(a.ang));
                    double a0 = a.x1 * ux + a.y1 * uy, a1 = a.x2 * ux + a.y2 * uy;
                    double b0 = b.x1 * ux + b.y1 * uy, b1 = b.x2 * ux + b.y2 * uy;
                    double ov = Math.min(Math.max(a0, a1), Math.max(b0, b1)) - Math.max(Math.min(a0, a1), Math.min(b0, b1));
                    if (ov < 0.6 * Math.min(a.len, b.len) || ov < minLen * 0.7) continue;
                    double sep = perpDist((a.x1 + a.x2) / 2, (a.y1 + a.y2) / 2, b);   // distance between the two edges
                    if (sep < minSep || sep > maxSep) continue;

                    double score = 0.45 * Math.min(1, ov / (0.35 * W)) + 0.30 * (1 - dAng / parTol) + 0.25 * (1 - sep / maxSep);
                    if (score < minScore) continue;
                    Seg low = (a.y1 + a.y2) > (b.y1 + b.y2) ? a : b;
                    double nearRow = Math.max(low.y1, low.y2);                    // lowest point of the curb = closest to the car
                    if (nearRow < H * p(cam, "minNearRow", 0.60)) continue;     // a curb far away near the horizon is not a zone (no curb NEAR the car)
                    if (yellowFraction(hsv, a, b) > p(cam, "yellowMax", 0.30)) continue;   // painted yellow line, NOT a curb
                    double rank = score + 0.5 * Math.min(1.0, nearRow / H);
                    if (rank > bestRank) {
                        bestRank = rank;
                        double xc = (Math.max(a.x1, b.x1) + Math.min(a.x2, b.x2)) / 2;
                        best = new Raw();
                        best.score = score; best.ang = low.ang; best.sep = sep; best.ov = ov;
                        best.rowBase = low.yAt(Math.max(low.x1, Math.min(low.x2, xc)));
                        best.line = new double[]{low.x1, low.y1, low.x2, low.y2};
                    }
                }
            }
            return best;
        } finally {
            gray.release(); hsv.release(); blur.release(); edges.release(); lines.release();
        }
    }

    /**
     * Share of yellow paint between the two edges (0..1). A painted yellow line (no-parking / road marking) also
     * looks like two parallel edges but is flat road paint, not a curb, so such pairs are thrown away.
     */
    private static double yellowFraction(Mat hsv, Seg a, Seg b) {
        int n = 0, yellow = 0;
        double l = Math.max(1e-6, b.len);
        for (int k = 0; k <= 24; k++) {
            double t = k / 24.0;
            double px = a.x1 + (a.x2 - a.x1) * t, py = a.y1 + (a.y2 - a.y1) * t;
            // foot of the perpendicular on b -> midpoint between the two edges
            double u = ((px - b.x1) * (b.x2 - b.x1) + (py - b.y1) * (b.y2 - b.y1)) / (l * l);
            double fx = b.x1 + u * (b.x2 - b.x1), fy = b.y1 + u * (b.y2 - b.y1);
            int mx = (int) Math.round((px + fx) / 2), my = (int) Math.round((py + fy) / 2);
            if (mx < 0 || my < 0 || mx >= hsv.cols() || my >= hsv.rows()) continue;
            double[] v = hsv.get(my, mx);                   // OpenCV HSV: H 0..180, S/V 0..255
            n++;
            if (v[0] >= 15 && v[0] <= 38 && v[1] >= 90 && v[2] >= 90) yellow++;
        }
        return n == 0 ? 0 : (double) yellow / n;
    }

    private static double perpDist(double px, double py, Seg s) {
        double l = Math.max(1e-6, s.len);
        return Math.abs((s.x2 - s.x1) * (s.y1 - py) - (s.x1 - px) * (s.y2 - s.y1)) / l;
    }


    // ---- debug: -Dwp5.vision.debug=true  -> prints WHY a curb was reported and saves a picture with the line drawn on it
    static final boolean DEBUG = Boolean.getBoolean("wp5.vision.debug");
    private static final String DEBUG_DIR = System.getProperty("wp5.vision.debugDir", "curb-debug");

    static void debugDump(Mat frame, Raw r, String cam, long n) {
        try {
            java.nio.file.Files.createDirectories(java.nio.file.Paths.get(DEBUG_DIR));
            Mat o = frame.clone();
            Imgproc.line(o, new Point(r.line[0], r.line[1]), new Point(r.line[2], r.line[3]), new Scalar(0, 0, 255), 3);
            Imgproc.putText(o, String.format("%s score=%.2f angle=%.0f sep=%.0fpx len=%.0fpx", cam, r.score, r.ang, r.sep, r.ov),
                    new Point(8, 20), Imgproc.FONT_HERSHEY_SIMPLEX, 0.5, new Scalar(0, 255, 255), 1);
            String f = DEBUG_DIR + "/" + cam + "_" + n + ".jpg";
            Imgcodecs.imwrite(f, o); o.release();
            System.out.println(String.format("[CURBVISION] %s: pair of parallel edges score=%.2f angle=%.0f deg separation=%.0fpx length=%.0fpx line=(%.0f,%.0f)-(%.0f,%.0f)  picture: %s",
                    cam, r.score, r.ang, r.sep, r.ov, r.line[0], r.line[1], r.line[2], r.line[3], f));
        } catch (Exception e) { System.err.println("[CURBVISION] debug failed: " + e); }
    }

    /** For tuning/testing: raw score of the best curb candidate in one frame, or -1 if none. */
    public static double debugScore(Mat bgr) { return debugScore(bgr, null); }
    public static double debugScore(Mat bgr, String cam) { Raw r = detectRaw(bgr, cam); return r == null ? -1 : r.score; }

    // ---- engine: segmentation model (if models/curb-seg.onnx exists) or the classic edge detector -----------------
    //   -Dwp5.vision.engine=auto (default) | seg | classic
    private static final CurbSegmenter SEG;
    static {
        String e = System.getProperty("wp5.vision.engine", "auto").trim().toLowerCase();
        SEG = "classic".equals(e) ? null : CurbSegmenter.tryLoad();
        System.out.println("[CURBVISION] engine: " + (SEG != null ? "segmentation model (road | sidewalk boundary)" : "classic edge-pair detector"));
    }
    /** true = a model is loaded: it is slower, so the video loop hands frames to a background thread (see CurbVisionWorker). */
    public static boolean useSegmenter() { return SEG != null; }

    /** {leftSeamPx, rightSeamPx}: width of the blended overlap at the left / right end of the picture being analysed (set by CurbPanorama). */
    static final ThreadLocal<int[]> SEAMS = new ThreadLocal<>();

    static Raw detectAny(Mat frame, String cam) { return SEG != null ? SEG.detect(frame, cam) : detectRaw(frame, cam); }

    /** Image row -> metres from the car body (linear; CALIBRATE rowAt0m / rowAtFar / farM per camera on real footage). */
    static double rowToMetres(double row, int frameH, String cam) {
        double r0 = p(cam, "rowAt0m", frameH * 0.97), rf = p(cam, "rowAtFar", frameH * 0.40), far = p(cam, "farM", 3.0);
        double d = (r0 - row) / (r0 - rf) * far;
        return Math.max(0, Math.min(3.0, d));
    }

    // ------------------------------------------------------------------ over time (one per side)

    /** Keeps a short history so one noisy frame never creates (or removes) a curb. One Tracker per camera. */
    public static final class Tracker {
        private final ArrayDeque<Boolean> hist = new ArrayDeque<>();
        private final String cam;
        private final int window, need;
        private double dist = Double.NaN, conf = 0; private double[] line; private long dbgN = 0;

        /** @param cam camera name: "left", "right", "front" or "rear" (selects the calibration) */
        public Tracker(String cam) {
            this.cam = cam;
            this.window = (int) p(cam, "window", 6);
            this.need = (int) p(cam, "need", 4);
        }

        /** Analyse one camera picture. */
        public Result update(Mat frame) {
            Raw r = detectAny(frame, cam);
            if (DEBUG && r != null && (dbgN++ % 20 == 0)) debugDump(frame, r, cam, dbgN);
            return updateRaw(r, frame.rows());
        }

        /** Smooth a candidate that was already found (null = nothing found in this picture). */
        Result updateRaw(Raw r, int frameRows) {
            hist.addLast(r != null);
            while (hist.size() > window) hist.removeFirst();
            if (r != null) {
                double d = rowToMetres(r.rowBase, frameRows, cam);
                boolean differs = line != null && disagree(line, r.line);
                if (differs && ++pending < 3) {
                    conf = 0.6 * conf + 0.4 * r.score;            // a different line in one picture only: keep the established curb line
                } else if (differs) {
                    dist = d; conf = r.score; line = r.line.clone(); pending = 0;   // it stays: switch to it (no mixing of two lines)
                } else {
                    pending = 0;
                    dist = Double.isNaN(dist) ? d : 0.6 * dist + 0.4 * d;
                    conf = 0.6 * conf + 0.4 * r.score;
                    line = line == null ? r.line.clone() : blend(line, r.line);
                }
            }
            int hits = 0; for (boolean b : hist) if (b) hits++;
            if (hits >= need && !Double.isNaN(dist)) return new Result(true, conf, dist, line.clone());
            if (hits == 0) { dist = Double.NaN; conf = 0; line = null; pending = 0; }
            return new Result(false, 0, Double.NaN, null);
        }
        private int pending = 0;
        /** Is b a different line than a (far away from it, or a clearly different direction)? */
        private static boolean disagree(double[] a, double[] b) {
            double ax = a[2] - a[0], ay = a[3] - a[1], al = Math.hypot(ax, ay);
            if (al < 1) return true;
            double mx = (b[0] + b[2]) / 2 - a[0], my = (b[1] + b[3]) / 2 - a[1];
            double perp = Math.abs(mx * ay - my * ax) / al;
            double da = Math.abs(Math.toDegrees(Math.atan2(ay, ax)) - Math.toDegrees(Math.atan2(b[3] - b[1], b[2] - b[0])));
            if (da > 180) da = 360 - da;
            if (da > 90) da = 180 - da;
            return perp > 25 || da > 15;
        }
        private static double[] blend(double[] a, double[] b) {
            double[] o = new double[4]; for (int i = 0; i < 4; i++) o[i] = 0.6 * a[i] + 0.4 * b[i]; return o;
        }
    }
}