package com.middleware.panorama.wp5;

import org.opencv.core.Core;
import org.opencv.core.CvType;
import org.opencv.core.Mat;
import org.opencv.core.MatOfPoint;
import org.opencv.core.MatOfPoint2f;
import org.opencv.core.Point;
import org.opencv.core.Rect;
import org.opencv.core.Scalar;
import org.opencv.core.Size;
import org.opencv.dnn.Dnn;
import org.opencv.dnn.Net;
import org.opencv.imgcodecs.Imgcodecs;
import org.opencv.imgproc.Imgproc;

import java.io.File;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Curb detection with a road-scene SEGMENTATION MODEL (any Cityscapes-style ONNX network, e.g. PIDNet-S, BiSeNet, SegFormer).
 *
 * The model labels every pixel (road, sidewalk, building, car, ...). A curb is the BOUNDARY between "road" and "sidewalk".
 * So this works in any of the four cameras and for any kind of curb the model has learned - no hand-made line rules.
 *
 *   frame (640x360 straightened)  ->  model  ->  label map  ->  sidewalk pixels that touch road pixels
 *                                  ->  longest, straightest boundary  ->  Raw{score,rowBase,line}
 *
 * The model file is NOT part of the project. Put it in   models/curb-seg.onnx   (or -Dwp5.seg.model=<path>).
 * If the file is missing, tryLoad() returns null and the classic edge detector (CurbVision) keeps working.
 *
 * Settings (-Dwp5.seg.<key>=value, or -Dwp5.seg.<camera>.<key>=value for one camera):
 *   model         path of the .onnx file                         (default models/curb-seg.onnx)
 *   inputW/inputH network input size                             (default 640 x 352; must suit your model, multiples of 32)
 *   mean / std    RGB normalisation, comma separated             (default 0.485,0.456,0.406 / 0.229,0.224,0.225 = ImageNet)
 *   roadClasses   class ids that are road                        (default 0         = Cityscapes "road")
 *   curbClasses   class ids on the far side of a curb            (default 1         = Cityscapes "sidewalk"; use 1,9 to add "terrain")
 *   roiTop/roiBottom  rows (0..1 of the frame height) that are searched (default 0.0 / 0.90: the car's own body is excluded)
 *   minLenFrac    shortest boundary as share of frame width      (default 0.15)
 *   minStraight   share of boundary pixels that lie on the fitted line (default 0.55)
 *   debug=true    save a picture (segmentation + curb line) to curb-debug/ about every 20th frame
 */
public final class CurbSegmenter {

    private final Net net;
    private final int inW, inH;
    private final double[] mean, std;
    private final Set<Integer> roadCls, curbCls;
    private long dbgN = 0;

    private CurbSegmenter(Net net, int inW, int inH, double[] mean, double[] std, Set<Integer> road, Set<Integer> curb) {
        this.net = net; this.inW = inW; this.inH = inH; this.mean = mean; this.std = std; this.roadCls = road; this.curbCls = curb;
    }

    // ------------------------------------------------------------------ settings

    private static String sp(String cam, String k, String d) {
        String v = cam == null ? null : System.getProperty("wp5.seg." + cam + "." + k);
        if (v == null) v = System.getProperty("wp5.seg." + k);
        return v == null ? d : v.trim();
    }
    private static double sd(String cam, String k, double d) { return Double.parseDouble(sp(cam, k, Double.toString(d))); }
    private static double[] triple(String s) {
        String[] t = s.split(","); double[] o = new double[3];
        for (int i = 0; i < 3; i++) o[i] = Double.parseDouble(t[i].trim());
        return o;
    }
    private static Set<Integer> ids(String s) {
        Set<Integer> o = new HashSet<>();
        for (String t : s.split(",")) if (!t.trim().isEmpty()) o.add(Integer.parseInt(t.trim()));
        return o;
    }

    /** @return the segmenter, or null when no model file exists / it cannot be read (then the classic detector is used). */
    public static CurbSegmenter tryLoad() {
        String path = sp(null, "model", "models/curb-seg.onnx");
        File f = new File(path);
        if (!f.isFile()) {
            System.out.println("[CURBSEG] no model at " + f.getAbsolutePath() + "  ->  classic edge detector is used");
            return null;
        }
        try {
            Net net = Dnn.readNetFromONNX(f.getAbsolutePath());
            if (net.empty()) throw new IllegalStateException("network is empty");
            CurbSegmenter s = new CurbSegmenter(net, (int) sd(null, "inputW", 640), (int) sd(null, "inputH", 352),
                    triple(sp(null, "mean", "0.485,0.456,0.406")), triple(sp(null, "std", "0.229,0.224,0.225")),
                    ids(sp(null, "roadClasses", "0")), ids(sp(null, "curbClasses", "1")));
            System.out.println("[CURBSEG] model loaded: " + f.getAbsolutePath() + "  input " + s.inW + "x" + s.inH
                    + "  road=" + s.roadCls + "  curb side=" + s.curbCls);
            return s;
        } catch (Exception | LinkageError e) {
            System.err.println("[CURBSEG] could not load " + f.getAbsolutePath() + ": " + e + "  ->  classic edge detector is used");
            return null;
        }
    }

    // ------------------------------------------------------------------ one frame

    /** Class id of every pixel of the frame, same size as the frame (CV_8U). */
    synchronized Mat labels(Mat bgr) {
        Mat rgb = new Mat(), small = new Mat(), f = new Mat(), blob = null, out = null;
        try {
            Imgproc.cvtColor(bgr, rgb, Imgproc.COLOR_BGR2RGB);
            Imgproc.resize(rgb, small, new Size(inW, inH));
            small.convertTo(f, CvType.CV_32FC3, 1.0 / 255.0);
            Core.subtract(f, new Scalar(mean[0], mean[1], mean[2]), f);
            Core.divide(f, new Scalar(std[0], std[1], std[2]), f);
            blob = Dnn.blobFromImage(f, 1.0, new Size(inW, inH), new Scalar(0, 0, 0), false, false);
            net.setInput(blob);
            out = net.forward();

            Mat lab8 = new Mat();
            int dims = out.dims();
            if (dims == 4) {                                    // [1, classes, H, W] scores -> best class per pixel
                int C = out.size(1), H = out.size(2), W = out.size(3);
                Mat flat = out.reshape(1, C);                   // C x (H*W)
                Mat arg = new Mat();
                Core.reduceArgMax(flat, arg, 0);                // 1 x (H*W) class ids
                Mat labSmall = arg.reshape(1, H);
                labSmall.convertTo(labSmall, CvType.CV_8U);
                Imgproc.resize(labSmall, lab8, new Size(bgr.cols(), bgr.rows()), 0, 0, Imgproc.INTER_NEAREST);
                flat.release(); arg.release(); labSmall.release();
            } else if (dims == 3 || dims == 2) {                // [1, H, W] or [H, W] class ids already
                int H = out.size(dims - 2), W = out.size(dims - 1);
                Mat labSmall = out.reshape(1, H);
                labSmall.convertTo(labSmall, CvType.CV_8U);
                Imgproc.resize(labSmall, lab8, new Size(bgr.cols(), bgr.rows()), 0, 0, Imgproc.INTER_NEAREST);
                labSmall.release();
            } else {
                throw new IllegalStateException("unexpected network output with " + dims + " dimensions");
            }
            return lab8;
        } finally {
            rgb.release(); small.release(); f.release(); if (blob != null) blob.release(); if (out != null) out.release();
        }
    }

    private static Mat maskOf(Mat labels, Set<Integer> classes) {
        Mat m = Mat.zeros(labels.size(), CvType.CV_8U), t = new Mat();
        for (int c : classes) { Core.inRange(labels, new Scalar(c), new Scalar(c), t); Core.bitwise_or(m, t, m); }
        t.release();
        return m;
    }

    /** Is there a curb (road | sidewalk boundary) in this frame? null = no. */
    CurbVision.Raw detect(Mat bgr, String cam) {
        if (bgr == null || bgr.empty() || bgr.channels() != 3) return null;
        final int W = bgr.cols(), H = bgr.rows();
        final int y0 = (int) (H * sd(cam, "roiTop", 0.0)), y1 = (int) (H * sd(cam, "roiBottom", 0.90));
        final double minLen = W * sd(cam, "minLenFrac", 0.15), minStraight = sd(cam, "minStraight", 0.55);

        Mat lab = labels(bgr);
        Mat road = maskOf(lab, roadCls), side = maskOf(lab, curbCls), roadD = new Mat(), edge = new Mat(), k = null;
        try {
            k = Imgproc.getStructuringElement(Imgproc.MORPH_RECT, new Size(5, 5));
            Imgproc.dilate(road, roadD, k);
            Core.bitwise_and(side, roadD, edge);                           // sidewalk pixels that touch the road = the curb line
            Imgproc.morphologyEx(edge, edge, Imgproc.MORPH_CLOSE, Imgproc.getStructuringElement(Imgproc.MORPH_RECT, new Size(3, 3)));
            int[] sm = CurbVision.SEAMS.get();                             // the blended seam between two cameras is not a curb
            if (sm != null) {
                if (sm[0] > 0) edge.colRange(0, Math.min(W, sm[0])).setTo(new Scalar(0));
                if (sm[1] > 0) edge.colRange(Math.max(0, W - sm[1]), W).setTo(new Scalar(0));
            }
            if (y0 > 0) edge.rowRange(0, y0).setTo(new Scalar(0));         // only the searched rows
            if (y1 < H) edge.rowRange(y1, H).setTo(new Scalar(0));         // (the car's own body is not a curb)

            Mat cl = new Mat(), st = new Mat(), ce = new Mat();
            int n = Imgproc.connectedComponentsWithStats(edge, cl, st, ce, 8);
            CurbVision.Raw best = null;
            for (int i = 1; i < n; i++) {                                  // every connected boundary piece
                int area = (int) st.get(i, Imgproc.CC_STAT_AREA)[0];
                if (area < 40) continue;
                Rect bb = new Rect((int) st.get(i, Imgproc.CC_STAT_LEFT)[0], (int) st.get(i, Imgproc.CC_STAT_TOP)[0],
                        (int) st.get(i, Imgproc.CC_STAT_WIDTH)[0], (int) st.get(i, Imgproc.CC_STAT_HEIGHT)[0]);
                if (Math.hypot(bb.width, bb.height) < minLen) continue;    // too short to be a curb
                Mat comp = new Mat(); Core.inRange(cl.submat(bb), new Scalar(i), new Scalar(i), comp);
                Mat pts = new Mat(); Core.findNonZero(comp, pts);
                if (pts.empty()) { comp.release(); pts.release(); continue; }
                MatOfPoint mp = new MatOfPoint(pts); Point[] pa = mp.toArray();
                MatOfPoint2f p2 = new MatOfPoint2f();
                Point[] shifted = new Point[pa.length];
                for (int j = 0; j < pa.length; j++) shifted[j] = new Point(pa[j].x + bb.x, pa[j].y + bb.y);
                p2.fromArray(shifted);
                Mat fl = new Mat(); Imgproc.fitLine(p2, fl, Imgproc.DIST_L2, 0, 0.01, 0.01);
                double vx = fl.get(0, 0)[0], vy = fl.get(1, 0)[0], x0 = fl.get(2, 0)[0], y0f = fl.get(3, 0)[0];
                double tmin = 1e9, tmax = -1e9; int inl = 0;
                for (Point q : shifted) {
                    double t = (q.x - x0) * vx + (q.y - y0f) * vy, dist = Math.abs((q.x - x0) * vy - (q.y - y0f) * vx);
                    tmin = Math.min(tmin, t); tmax = Math.max(tmax, t); if (dist <= 4) inl++;
                }
                double length = tmax - tmin, straight = (double) inl / shifted.length;
                comp.release(); pts.release(); mp.release(); p2.release(); fl.release();
                if (length < minLen || straight < minStraight) continue;
                double score = 0.5 * Math.min(1.0, length / (0.5 * W)) + 0.5 * straight;
                if (best == null || score > best.score) {
                    best = new CurbVision.Raw();
                    best.score = score; best.rowBase = y0f; best.ov = length; best.sep = 0;
                    best.ang = Math.toDegrees(Math.atan2(vy, vx));
                    best.line = new double[]{x0 + vx * tmin, y0f + vy * tmin, x0 + vx * tmax, y0f + vy * tmax};
                }
            }
            cl.release(); st.release(); ce.release();
            if (Boolean.parseBoolean(sp(cam, "debug", "false")) && best != null && (dbgN++ % 20 == 0)) debugDump(bgr, lab, best, cam, dbgN);
            return best;
        } finally {
            lab.release(); road.release(); side.release(); roadD.release(); edge.release(); if (k != null) k.release();
        }
    }

    private void debugDump(Mat bgr, Mat lab, CurbVision.Raw r, String cam, long n) {
        try {
            new File("curb-debug").mkdirs();
            Mat o = bgr.clone(), road = maskOf(lab, roadCls), side = maskOf(lab, curbCls), tint = bgr.clone();
            tint.setTo(new Scalar(255, 0, 0), road);          // road = blue
            tint.setTo(new Scalar(0, 200, 0), side);          // sidewalk = green
            Core.addWeighted(bgr, 0.6, tint, 0.4, 0, o);
            Imgproc.line(o, new Point(r.line[0], r.line[1]), new Point(r.line[2], r.line[3]), new Scalar(0, 255, 255), 3);
            Imgproc.putText(o, String.format("%s score=%.2f len=%.0fpx", cam, r.score, r.ov), new Point(8, 20), Imgproc.FONT_HERSHEY_SIMPLEX, 0.5, new Scalar(0, 255, 255), 1);
            Imgcodecs.imwrite("curb-debug/seg_" + cam + "_" + n + ".jpg", o);
            o.release(); road.release(); side.release(); tint.release();
        } catch (Exception e) { System.err.println("[CURBSEG] debug failed: " + e); }
    }

    // ------------------------------------------------------------------ test one picture from the command line
    //   java -cp <classes>:<opencv.jar> com.middleware.panorama.wp5.CurbSegmenter picture.png [camera]
    public static void main(String[] a) {
        if (a.length < 1) { System.out.println("usage: CurbSegmenter <picture> [camera]"); return; }
        try { nu.pattern.OpenCV.loadLocally(); } catch (Throwable t) { System.loadLibrary(Core.NATIVE_LIBRARY_NAME); }
        CurbSegmenter s = tryLoad();
        if (s == null) return;
        Mat img = Imgcodecs.imread(a[0]);
        Imgproc.resize(img, img, new Size(640, 360));
        String cam = a.length > 1 ? a[1] : null;
        CurbVision.Raw r = s.detect(img, cam);
        if (r == null) { System.out.println("no curb found in " + a[0]); return; }
        System.out.printf("CURB found: score=%.2f length=%.0fpx angle=%.0f deg  line=(%.0f,%.0f)-(%.0f,%.0f)  distance=%.2f m%n",
                r.score, r.ov, r.ang, r.line[0], r.line[1], r.line[2], r.line[3], CurbVision.rowToMetres(r.rowBase, 360, cam));
        Mat lab = s.labels(img); s.debugDump(img, lab, r, cam == null ? "test" : cam, 0);
        System.out.println("picture saved in curb-debug/ (blue = road, green = sidewalk, yellow = curb line)");
    }
}
