package com.middleware.panorama;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

import java.io.File;
import java.io.IOException;
import java.io.OutputStream;
import java.net.BindException;
import java.net.InetSocketAddress;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executors;

import com.middleware.panorama.wp5.CurbVisionWorker;
import com.middleware.panorama.wp5.VisionSidecar;
import com.middleware.panorama.wp5.Wp5Service;

import org.opencv.calib3d.Calib3d;
import org.opencv.core.*;
import org.opencv.imgcodecs.Imgcodecs;
import org.opencv.imgproc.Imgproc;
import org.opencv.videoio.VideoCapture;
import org.opencv.videoio.Videoio;

public class VideoStreamingServer {

    private static final int DEFAULT_PORT  = 9090;
    private static final int TARGET_HEIGHT = 360;
    private static final int TARGET_WIDTH  = 640;
    private static final int OVERLAP_PX    = 80;
    /**
     * Which video goes in which panorama panel (left -> right) and which physical camera it is.
     * Format "<video number>:<role>", roles = front | rear | left | right (WP5 needs exactly these
     * names to place the curb zones). Override: -Dpanels="1:front,4:right,2:rear,3:left"
     *
     * Default = ring order around the car, so neighbouring panels really touch each other
     * (front|right|rear|left, and left wraps to front). Identified from the clips:
     *   1.mp4 front, 4.mp4 right, 2.mp4 rear (bumper + number plate visible), 3.mp4 left.
     */
    private static final String DEFAULT_PANELS = "3:left,1:front,4:right,2:rear,";
    private static final java.util.Set<String> ROLES =
            new java.util.HashSet<>(java.util.Arrays.asList("left", "front", "right", "rear"));

    /** Panel index (0..3) that shows the REAR camera -> gets RearFeedPipeline. Set in main(). */
    private static volatile int rearPanel = 2;

    /** -Dvideo.realtime=false: decode as fast as possible instead of pacing to the clips' fps. */
    private static final boolean REALTIME = !"false".equalsIgnoreCase(System.getProperty("video.realtime"));

    // =========================================================================
    // ---- curb zones come from the CONTENT of the camera frames --------------------------------
    //   -Dwp5.vision.detector=false              switch the detector off (zones then only via the viewer's TEST controls)
    //   -Dwp5.vision.sides=left,right,front,rear which cameras are analysed (default: all four)
    private static volatile Wp5Service WP5;
    private static final boolean VISION_ON = !"false".equalsIgnoreCase(System.getProperty("wp5.vision.detector", "true"));
    private static final java.util.Set<String> VISION_SIDES = new java.util.HashSet<>(java.util.Arrays.asList(
            System.getProperty("wp5.vision.sides", "left,right,front,rear").toLowerCase().replace(" ", "").split(",")));
    private static final int VISION_EVERY = Integer.getInteger("wp5.vision.everyNthFrame", 3);   // analyse every 3rd frame (~10 Hz)

    // one background worker for the whole server (used only when a segmentation model is loaded)
    private static CurbVisionWorker SEG_WORKER;
    private static synchronized CurbVisionWorker segWorker() {
        if (SEG_WORKER == null) {
            SEG_WORKER = new CurbVisionWorker(WP5_CAMERAS, TARGET_WIDTH, OVERLAP_PX, VISION_SIDES,
                    (cam, r) -> { if (VisionSidecar.classicAllowed()) WP5.onVision(cam, r.detected, r.distanceM, r.confidence, r.line, cam); });
        }
        return SEG_WORKER;
    }
    private static String[] WP5_CAMERAS = new String[0];

    public static void main(String[] args) throws IOException {

        // ---- 1. panels: which video file is which camera, in panorama order --------------------
        String[] spec = System.getProperty("panels", DEFAULT_PANELS).split(",");
        if (spec.length != 4) {
            System.err.println("-Dpanels needs exactly 4 entries like 1:front,4:right,2:rear,3:left");
            return;
        }
        String[] numbers = new String[4];
        String[] cameraNames = new String[4];                     // panel order, handed to WP5
        java.util.Set<String> seen = new java.util.HashSet<>();
        for (int i = 0; i < 4; i++) {
            String[] kv = spec[i].trim().split(":");
            if (kv.length != 2 || !ROLES.contains(kv[1].trim().toLowerCase()) || !seen.add(kv[1].trim().toLowerCase())) {
                System.err.println("Bad -Dpanels entry '" + spec[i] + "' (use <video number>:<front|rear|left|right>, each role once)");
                return;
            }
            numbers[i] = kv[0].trim();
            cameraNames[i] = kv[1].trim().toLowerCase();
            if (cameraNames[i].equals("rear")) rearPanel = i;
        }

        String ext = System.getProperty("video.ext", "mp4");
        Path dir = findVideoDir(numbers, ext);
        if (dir == null && args.length < 5) {
            System.err.println("Could not find " + numbers[0] + "." + ext + " ... in src/main/java or the current folder."
                    + " Use -Dvideo.dir=<folder> or pass 4 file paths after the port.");
            return;
        }
        Path[] videos = new Path[4];
        for (int i = 0; i < 4; i++) {
            Path p = args.length >= 5 ? Paths.get(args[1 + i]) : dir.resolve(numbers[i] + "." + ext);
            videos[i] = ensureDecodable(p);
        }
        for (Path v : videos) {
            if (!Files.exists(v) || Files.isDirectory(v)) {
                System.err.println("Video file not found: " + v);
                return;
            }
        }

        int port = args.length > 0 ? Integer.parseInt(args[0]) : DEFAULT_PORT;
        if (port < 0 || port > 65535) {
            System.err.println("Invalid port number: " + port);
            return;
        }

        // ---- 2. OpenCV native library (your official build first, bundled jar as fallback) -----
        try {
            System.loadLibrary(Core.NATIVE_LIBRARY_NAME);
        } catch (UnsatisfiedLinkError e) {
            try {
                nu.pattern.OpenCV.loadLocally();
            } catch (Throwable t) {
                System.err.println("OpenCV native library not found: " + e.getMessage());
                return;
            }
        }
        loadFfmpegPlugin();

        // ---- 3. HTTP server --------------------------------------------------------------------
        HttpServer server;
        try {
            server = HttpServer.create(new InetSocketAddress(port), 0);
        } catch (BindException e) {
            System.err.println("Port " + port + " is already in use. Stop the other server (Ctrl+C) or use another port.");
            return;
        }

        server.createContext("/stitch", new StitchHandler(videos, cameraNames));
        server.createContext("/play",   new PlayerPageHandler());
        server.createContext("/meta",   new CommonHandlers.MetaHandler(port, cameraNames,
                TARGET_WIDTH, TARGET_HEIGHT, OVERLAP_PX));

        // WP5: steering / wheel + curb-zone signals. cameraNames MUST be in panel order.
        WP5 = Wp5Service.attach(server, cameraNames, TARGET_WIDTH, TARGET_HEIGHT, OVERLAP_PX);
        WP5_CAMERAS = cameraNames.clone();
        System.out.println("WP5 zones from  ->  " + (VISION_ON ? "STITCHED PANORAMA (curb vision on: " + VISION_SIDES + ")" : "hand-made test input only (detector off)"));

        // Every /stitch (MJPEG) and /api/v1/signals (SSE) client holds a thread for as long as it
        // is connected. A fixed pool of 4 is used up by the viewer + the phone, and then even the
        // ingest POSTs hang. A cached pool grows on demand.
        server.setExecutor(Executors.newCachedThreadPool());
        server.start();

        System.out.println("Server started  ->  http://localhost:" + port + "/play");
        System.out.println("WP5 viewer      ->  http://localhost:" + port + "/wp5/viewer");
        System.out.println("Signals (SSE)   ->  http://localhost:" + port + "/api/v1/signals");
        System.out.println("Panorama order (left -> right):");
        for (int i = 0; i < 4; i++) {
            System.out.println("  panel " + i + ": " + cameraNames[i] + "  <-  " + videos[i].toAbsolutePath()
                    + (i == rearPanel ? "   (rear pipeline: look-up + crop)" : ""));
        }
    }

    /** Folder that contains all four numbered clips: -Dvideo.dir, then src/main/java, then ".". */
    private static Path findVideoDir(String[] numbers, String ext) {
        List<Path> cands = new ArrayList<>();
        String prop = System.getProperty("video.dir");
        if (prop != null && !prop.trim().isEmpty()) cands.add(Paths.get(prop.trim()));
        cands.add(Paths.get("src", "main", "java"));
        cands.add(Paths.get("."));
        for (Path d : cands) {
            boolean all = true;
            for (String n : numbers) {
                if (!isUsableFile(d.resolve(n + "." + ext)) && !isUsableFile(d.resolve(n + ".mov"))) { all = false; break; }
            }
            if (all) return d;
        }
        return null;
    }

    // =========================================================================
    // STITCH HANDLER  -  undistort each feed, then feather-blend panorama

    private static class StitchHandler implements HttpHandler {
      private final Path[] videoFiles;
      private final String[] cameraNames;            // same order as videoFiles: front / right / rear / left (see DEFAULT_PANELS)
      StitchHandler(Path[] f, String[] names) { this.videoFiles = f; this.cameraNames = names; }

      @Override public void handle(HttpExchange ex) throws IOException {
        if (!"GET".equalsIgnoreCase(ex.getRequestMethod())) {
          ex.sendResponseHeaders(405, -1); return;
        }

        VideoCapture[] caps = new VideoCapture[videoFiles.length];
        for (int i = 0; i < videoFiles.length; i++) {
          caps[i] = openVideo(videoFiles[i]);
          if (caps[i] == null || !caps[i].isOpened()) {
            System.err.println("Could not open video: " + videoFiles[i]);
            ex.sendResponseHeaders(500, -1); return;
          }
        }

        CameraFeedFilter[] undistort = new CameraFeedFilter[videoFiles.length];
        for (int i = 0; i < undistort.length; i++) {
          undistort[i] = (i == rearPanel)
              ? new RearFeedPipeline()
              : new FisheyeUndistorter();
        }

        ex.getResponseHeaders().set("Content-Type", "multipart/x-mixed-replace; boundary=frame");
        ex.sendResponseHeaders(200, 0);

        try (OutputStream out = ex.getResponseBody()) {
          Mat[] frames  = new Mat[videoFiles.length];
          Mat[] ready   = new Mat[videoFiles.length];
          for (int i = 0; i < videoFiles.length; i++) {
            frames[i] = new Mat();
            ready[i]  = new Mat();
          }

          long visionTick = 0;
          long sidecarTick = 0;
          double srcFps = caps[0].get(Videoio.CAP_PROP_FPS);
          long frameMs = REALTIME ? (long) (1000.0 / ((srcFps >= 1 && srcFps <= 120) ? srcFps : 30.0)) : 0;
          long nextAt = System.currentTimeMillis();

          while (true) {
            for (int i = 0; i < caps.length; i++) {
              if (!readOrLoop(caps, i, videoFiles[i], frames[i])) {
                continue;
              }
              undistort[i].recalibrateAndFilter(frames[i], ready[i]);
            }

            boolean allReady = true;
            for (int i = 0; i < ready.length; i++) {
              if (ready[i].empty()
                  || ready[i].cols() != TARGET_WIDTH
                  || ready[i].rows() != TARGET_HEIGHT) {
                allReady = false;
                break;
              }
            }
            if (!allReady) {
              try { Thread.sleep(40); } catch (InterruptedException ie) { Thread.currentThread().interrupt(); break; }
              continue;                      // (was a 100 % CPU busy-loop when a clip could not be read)
            }

            Mat panorama = featherStitch(ready);
            // curb detection works on the STITCHED PANORAMA (background thread, the video does not wait for it)
            if (VISION_ON && WP5 != null && (visionTick++ % VISION_EVERY == 0)) segWorker().submit(panorama);
            // optional: also hand every straightened camera picture to the Python vision sidecar (only if -Dwp5.vision.engine=sidecar|auto)
            if (VisionSidecar.enabled() && WP5 != null && (sidecarTick++ % VISION_EVERY == 0))
                for (int i = 0; i < ready.length && i < WP5_CAMERAS.length; i++) VisionSidecar.get().offer(WP5_CAMERAS[i], ready[i]);
            writeFrame(out, encodeJpeg(panorama));
            panorama.release();

            if (frameMs > 0) {               // play at the clips' own speed (30 fps) instead of flat-out
              nextAt += frameMs;
              long wait = nextAt - System.currentTimeMillis();
              if (wait > 0) {
                try { Thread.sleep(wait); } catch (InterruptedException ie) { Thread.currentThread().interrupt(); break; }
              } else {
                nextAt = System.currentTimeMillis();
              }
            }
          }
        }
        finally {
          for (VideoCapture c : caps) c.release();
        }

      } // handle()
    } // StitchHandler


    // PLAYER PAGE  -  stitched panorama only, full-viewport

    private static class PlayerPageHandler implements HttpHandler {

        @Override public void handle(HttpExchange ex) throws IOException {
            String html = "<!DOCTYPE html><html lang='en'><head>"
                + "<meta charset='UTF-8'>"
                + "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                + "<title>360° Panoramic View</title>"
                + "<style>"
                + "*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }"
                + "html, body { height: 100%; background: #0a0a0f; color: #e0e0e0;"
                + "  font-family: 'Segoe UI', sans-serif; overflow: hidden; }"
                + ".container { display: flex; flex-direction: column;"
                + "  align-items: center; justify-content: center;"
                + "  height: 100vh; padding: 16px; gap: 12px; }"
                + "h1 { font-size: 1.4rem; font-weight: 300; letter-spacing: 2px;"
                + "  color: #7ec8e3; text-align: center; flex-shrink: 0; }"
                + ".pano-wrap { width: 100%; flex: 1; min-height: 0;"
                + "  border: 1px solid #2a2a3a; border-radius: 8px; overflow: hidden;"
                + "  display: flex; align-items: center; justify-content: center; }"
                + ".pano-wrap img { width: 100%; height: 100%; object-fit: contain; display: block; }"
                + "</style>"
                + "</head><body>"
                + "<div class='container'>"
                + "  <h1>360° Panoramic Camera System</h1>"
                + "  <div class='pano-wrap'>"
                + "    <img src='/stitch' alt='360° stitched panorama'>"
                + "  </div>"
                + "</div>"
                + "</body></html>";

            byte[] bytes = html.getBytes("UTF-8");
            ex.getResponseHeaders().set("Content-Type", "text/html; charset=UTF-8");
            ex.sendResponseHeaders(200, bytes.length);
            try (OutputStream os = ex.getResponseBody()) { os.write(bytes); }
        }
    }


    interface CameraFeedFilter {
        void recalibrateAndFilter(Mat src, Mat dst360x640);
    }

    //  FISHEYE UNDISTORT LAYER
    //
    //  Size-adaptive: K is rebuilt from frame size. Do not send 180° fisheye
    //  through a pinhole model — tan(90°) is infinite and produces the
    //  radial starburst. Output is a finite rectilinear crop (default 90°).

    static final class FisheyeUndistorter implements CameraFeedFilter {

        /** Assumed diagonal-ish horizontal coverage of the raw fisheye. Keep < 170. */
        private static final double INPUT_FOV_DEG = 150.0;

        /** Rectilinear view sent to stitch. 80–100 is typical; lower = more zoom. */
        private static final double OUTPUT_FOV_DEG = 90.0;

        private static final double[] FISHEYE_D = { 0.0, 0.0, 0.0, 0.0 };

        private Mat map1;
        private Mat map2;
        private Mat undistorted;
        private Mat filtered;
        private int cachedSrcW = -1;
        private int cachedSrcH = -1;

        @Override
        public void recalibrateAndFilter(Mat src, Mat dst360x640) {
            if (src == null || src.empty()) {
                return;
            }

            ensureMaps(src.cols(), src.rows());

            if (undistorted == null) undistorted = new Mat();
            if (filtered == null)    filtered    = new Mat();

            Imgproc.remap(src, undistorted, map1, map2, Imgproc.INTER_LINEAR,
                    Core.BORDER_CONSTANT);

            Imgproc.GaussianBlur(undistorted, filtered, new Size(3, 3), 0.6);

            Size target = new Size(TARGET_WIDTH, TARGET_HEIGHT);
            if (filtered.cols() == TARGET_WIDTH && filtered.rows() == TARGET_HEIGHT) {
                filtered.copyTo(dst360x640);
            } else {
                Imgproc.resize(filtered, dst360x640, target, 0, 0, Imgproc.INTER_AREA);
            }
        }

        private void ensureMaps(int srcW, int srcH) {
            if (map1 != null && srcW == cachedSrcW && srcH == cachedSrcH) {
                return;
            }

            Size dstSize = new Size(TARGET_WIDTH, TARGET_HEIGHT);

            Mat K = equidistantK(srcW, srcH, INPUT_FOV_DEG);
            Mat D = distortionCoeffs();
            Mat R = Mat.eye(3, 3, CvType.CV_64FC1);
            Mat P = pinholeK(TARGET_WIDTH, TARGET_HEIGHT, OUTPUT_FOV_DEG);

            if (map1 == null) map1 = new Mat();
            if (map2 == null) map2 = new Mat();

            Calib3d.fisheye_initUndistortRectifyMap(
                    K, D, R, P, dstSize, CvType.CV_16SC2, map1, map2);

            cachedSrcW = srcW;
            cachedSrcH = srcH;

            K.release();
            D.release();
            R.release();
            P.release();
        }

        /** r = f * theta. fx == fy so aspect ratio is preserved. */
        static Mat equidistantK(int width, int height, double fovDeg) {
            double half = Math.toRadians(fovDeg) / 2.0;
            double f = (Math.max(width, height) / 2.0) / half;
            return matrixK(f, f, width / 2.0, height / 2.0);
        }

        /** r = f * tan(theta). FOV must stay well below 180°. */
        static Mat pinholeK(int width, int height, double fovDeg) {
            double half = Math.toRadians(fovDeg) / 2.0;
            double f = (width / 2.0) / Math.tan(half);
            return matrixK(f, f, width / 2.0, height / 2.0);
        }

        static Mat matrixK(double fx, double fy, double cx, double cy) {
            Mat K = Mat.eye(3, 3, CvType.CV_64FC1);
            K.put(0, 0, fx);
            K.put(1, 1, fy);
            K.put(0, 2, cx);
            K.put(1, 2, cy);
            return K;
        }

        static Mat distortionCoeffs() {
            Mat D = new Mat(4, 1, CvType.CV_64FC1);
            D.put(0, 0, FISHEYE_D[0]);
            D.put(1, 0, FISHEYE_D[1]);
            D.put(2, 0, FISHEYE_D[2]);
            D.put(3, 0, FISHEYE_D[3]);
            return D;
        }

        static Mat eulerRyxz(double pitchDeg, double yawDeg, double rollDeg) {
            Mat rvec = new Mat(3, 1, CvType.CV_64FC1);
            rvec.put(0, 0, Math.toRadians(pitchDeg));
            rvec.put(1, 0, Math.toRadians(yawDeg));
            rvec.put(2, 0, Math.toRadians(rollDeg));
            Mat R = new Mat();
            Calib3d.Rodrigues(rvec, R);
            rvec.release();
            return R;
        }
    }


    //  REAR CAMERA PIPELINE
    //
    //  The rear fisheye sits low and looks down: bumper / plate fill the bottom
    //  of the circle and the street sits in the upper FOV. A separate pitch-up
    //  remap + top crop keeps horizon/road and drops the number plate.
    //  Only this block is used for the panel whose role is "rear" (see DEFAULT_PANELS).

    static final class RearFeedPipeline implements CameraFeedFilter {

        /** Degrees to tilt the virtual camera toward the top of the raw frame. */
        private static final double LOOK_UP_DEG = 18.0;

        /** Clockwise-positive in the image. Set negative to counter a clockwise roll. */
        private static final double ROLL_DEG = 0.0;

        private static final double INPUT_FOV_DEG  = 160.0;
        private static final double OUTPUT_FOV_DEG = 88.0;

        /** Skip this much from the top of the remapped frame (camera housing). */
        private static final double TOP_SKIP_FRACTION = 0.10;

        /**
         * Vertical window after skip. Higher includes more road / bumper;
         * lower stays on trees/sky.
         */
        private static final double KEEP_FRACTION = 0.72;

        private Mat map1;
        private Mat map2;
        private Mat undistorted;
        private int cachedSrcW = -1;
        private int cachedSrcH = -1;
        private int mapH = TARGET_HEIGHT;

        @Override
        public void recalibrateAndFilter(Mat src, Mat dst360x640) {
            if (src == null || src.empty()) {
                return;
            }

            ensureMaps(src.cols(), src.rows());

            if (undistorted == null) undistorted = new Mat();

            Imgproc.remap(src, undistorted, map1, map2, Imgproc.INTER_LINEAR);

            int h = undistorted.rows();
            int skip = (int) Math.round(h * TOP_SKIP_FRACTION);
            int keepH = Math.max(1, (int) Math.round(h * KEEP_FRACTION));
            if (skip + keepH > h) {
                keepH = h - skip;
            }
            if (keepH < 1) {
                skip = 0;
                keepH = h;
            }
            Mat window = undistorted.rowRange(skip, skip + keepH);
            Imgproc.resize(window, dst360x640, new Size(TARGET_WIDTH, TARGET_HEIGHT),
                    0, 0, Imgproc.INTER_AREA);
        }

        private void ensureMaps(int srcW, int srcH) {
            if (map1 != null && srcW == cachedSrcW && srcH == cachedSrcH) {
                return;
            }

            mapH = (int) Math.round(TARGET_HEIGHT / KEEP_FRACTION);
            Size dstSize = new Size(TARGET_WIDTH, mapH);

            Mat K = FisheyeUndistorter.equidistantK(srcW, srcH, INPUT_FOV_DEG);
            Mat D = FisheyeUndistorter.distortionCoeffs();
            // Negative pitch (Y-down camera) aims the virtual view at the top of the fisheye.
            Mat R = FisheyeUndistorter.eulerRyxz(-LOOK_UP_DEG, 0.0, ROLL_DEG);
            Mat P = FisheyeUndistorter.pinholeK(TARGET_WIDTH, mapH, OUTPUT_FOV_DEG);

            if (map1 == null) map1 = new Mat();
            if (map2 == null) map2 = new Mat();

            Calib3d.fisheye_initUndistortRectifyMap(
                    K, D, R, P, dstSize, CvType.CV_16SC2, map1, map2);

            cachedSrcW = srcW;
            cachedSrcH = srcH;

            K.release();
            D.release();
            R.release();
            P.release();
        }
    }


    //  CORE BLENDING  —  featherStitch

    static Mat featherStitch(Mat[] frames) {
        int N = frames.length;
        int H = TARGET_HEIGHT;
        int W = TARGET_WIDTH;

        int overlap = Math.min(OVERLAP_PX, W / 4);
        int panoW   = W + (N - 1) * (W - overlap);

        Mat accumColor  = Mat.zeros(H, panoW, CvType.CV_32FC3);
        Mat accumWeight = Mat.zeros(H, panoW, CvType.CV_32FC1);

        for (int i = 0; i < N; i++) {
            int xStart = i * (W - overlap);

            Mat weight = buildFeatherMask(H, W, overlap);

            Mat frameF = new Mat();
            frames[i].convertTo(frameF, CvType.CV_32FC3);

            Mat weight3 = new Mat();
            List<Mat> ch = new ArrayList<>();
            ch.add(weight); ch.add(weight); ch.add(weight);
            Core.merge(ch, weight3);

            Mat wFrame = new Mat();
            Core.multiply(frameF, weight3, wFrame);

            int xEnd    = Math.min(xStart + W, panoW);
            int wActual = xEnd - xStart;

            Mat colorRoi  = accumColor.submat(0, H, xStart, xEnd);
            Mat weightRoi = accumWeight.submat(0, H, xStart, xEnd);

            Mat wFrameCrop = wFrame.colRange(0, wActual);
            Mat weightCrop = weight.colRange(0, wActual);

            Core.add(colorRoi,  wFrameCrop, colorRoi);
            Core.add(weightRoi, weightCrop, weightRoi);

            colorRoi.release(); weightRoi.release();
            frameF.release(); weight.release(); weight3.release();
            wFrame.release();
        }

        Mat safeW = new Mat();
        Core.max(accumWeight, new Scalar(1e-6), safeW);

        Mat safeW3 = new Mat();
        List<Mat> wch = new ArrayList<>();
        wch.add(safeW); wch.add(safeW); wch.add(safeW);
        Core.merge(wch, safeW3);

        Mat blended = new Mat();
        Core.divide(accumColor, safeW3, blended);

        Mat result = new Mat();
        blended.convertTo(result, CvType.CV_8UC3);

        accumColor.release(); accumWeight.release();
        safeW.release(); safeW3.release(); blended.release();

        return result;
    }

    static Mat buildFeatherMask(int H, int W, int overlap) {
        Mat mask = new Mat(H, W, CvType.CV_32FC1, new Scalar(1.0));
        for (int x = 0; x < overlap; x++) {
            float alpha = (float) x / overlap;
            for (int y = 0; y < H; y++) {
                mask.put(y, x,          new float[]{ alpha });
                mask.put(y, W - 1 - x,  new float[]{ alpha });
            }
        }
        return mask;
    }


    // VIDEO IO  —  FFmpeg plugin, then MSMF without RGB32 conversion

    static Path preferH264(Path requested) {
        return ensureDecodable(requested);
    }

    /**
     * These camera .mov files are PNG video (FFmpeg codec_id=61, fourcc png).
     * OpenCV's bundled FFmpeg and Windows MSMF cannot decode that.
     * Use a sibling H.264 .mp4, transcoding with ffmpeg when needed.
     */
    static Path ensureDecodable(Path requested) {
        String lower = requested.getFileName().toString().toLowerCase();
        if (!lower.endsWith(".mov") && isUsableFile(requested)) {
            return requested;                  // mp4 / avi / mkv: use as is, no message, no transcode
        }
        Path mp4 = siblingWithExt(requested, ".mp4");
        if (isUsableFile(mp4)) {
            System.out.println("Using " + mp4.getFileName() + " (H.264) instead of "
                    + requested.getFileName());
            return mp4;
        }
        if (!isUsableFile(requested)) {
            return requested;
        }
        if (transcodeToH264(requested, mp4) && isUsableFile(mp4)) {
            return mp4;
        }
        System.err.println("Cannot decode " + requested.getFileName()
                + " (PNG-in-MOV). Install ffmpeg on PATH and re-run, or convert:");
        System.err.println("  ffmpeg -y -i " + requested.getFileName()
                + " -c:v libx264 -pix_fmt yuv420p -an " + mp4.getFileName());
        return requested;
    }

    static Path siblingWithExt(Path requested, String ext) {
        String name = requested.getFileName().toString();
        int dot = name.lastIndexOf('.');
        String stem = dot >= 0 ? name.substring(0, dot) : name;
        Path dir = requested.toAbsolutePath().getParent();
        if (dir == null) {
            dir = Paths.get(".");
        }
        return dir.resolve(stem + ext);
    }

    static boolean isUsableFile(Path path) {
        try {
            return path != null && Files.isRegularFile(path) && Files.size(path) > 0;
        } catch (IOException e) {
            return false;
        }
    }

    static boolean transcodeToH264(Path src, Path dst) {
        System.out.println("Transcoding " + src.getFileName() + " -> " + dst.getFileName()
                + " (PNG MOV cannot be decoded by OpenCV FFmpeg/MSMF)");
        ProcessBuilder pb = new ProcessBuilder(
                "ffmpeg", "-hide_banner", "-y",
                "-i", src.toAbsolutePath().toString(),
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-an", dst.toAbsolutePath().toString());
        pb.inheritIO();
        try {
            int code = pb.start().waitFor();
            if (code == 0 && isUsableFile(dst)) {
                System.out.println("Transcode OK: " + dst.getFileName());
                return true;
            }
            System.err.println("ffmpeg exited with code " + code);
        } catch (IOException e) {
            System.err.println("ffmpeg not found on PATH: " + e.getMessage());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            System.err.println("ffmpeg transcode interrupted");
        }
        try {
            Files.deleteIfExists(dst);
        } catch (IOException ignored) {
        }
        return false;
    }

    /**
     * Official Windows OpenCV puts opencv_java*.dll in build/java/x64 and the
     * FFmpeg videoio plugin in build/bin. java.library.path usually only has
     * the first folder, so MSMF is used and .mov RGB32 decode fails.
     */
    static void loadFfmpegPlugin() {
        String[] dllNames = {
            "opencv_videoio_ffmpeg490_64.dll",
            "opencv_videoio_ffmpeg4.dll",
            "opencv_videoio_ffmpeg.dll"
        };
        List<Path> dirs = new ArrayList<>();
        String libPath = System.getProperty("java.library.path", "");
        for (String dir : libPath.split(File.pathSeparator)) {
            if (dir == null || dir.trim().isEmpty()) continue;
            Path p = Paths.get(dir.trim()).toAbsolutePath().normalize();
            dirs.add(p);
            if (p.getParent() != null) {
                dirs.add(p.getParent());
                if (p.getParent().getParent() != null) {
                    dirs.add(p.getParent().getParent().resolve("bin"));
                }
            }
        }
        dirs.add(Paths.get("opencv", "build", "bin").toAbsolutePath());
        dirs.add(Paths.get("..", "opencv", "build", "bin").toAbsolutePath());

        for (Path dir : dirs) {
            for (String dll : dllNames) {
                Path candidate = dir.resolve(dll).normalize();
                if (!Files.isRegularFile(candidate)) continue;
                try {
                    System.load(candidate.toString());
                    System.out.println("Loaded FFmpeg videoio plugin: " + candidate);
                    return;
                } catch (Throwable t) {
                    System.err.println("Could not load " + candidate + ": " + t.getMessage());
                }
            }
        }
        System.err.println("FFmpeg videoio plugin not loaded. MSMF will be used for .mov "
                + "and may fail RGB32. Copy opencv_videoio_ffmpeg490_64.dll next to "
                + "opencv_java490.dll or into opencv\\build\\bin on PATH.");
    }

    static VideoCapture openVideo(Path path) {
        String file = path.toAbsolutePath().toString();

        int[] apis = {
            Videoio.CAP_FFMPEG,
            Videoio.CAP_ANY,
            Videoio.CAP_MSMF
        };
        String[] labels = { "FFMPEG", "ANY", "MSMF" };

        for (int a = 0; a < apis.length; a++) {
            for (double convert : new double[]{ (apis[a] == Videoio.CAP_MSMF ? 0 : 1), 0, 1 }) {
                VideoCapture cap = tryOpen(file, apis[a], labels[a], convert);
                if (cap != null) {
                    return cap;
                }
            }
        }

        VideoCapture cap = new VideoCapture();
        cap.open(file);
        if (cap.isOpened()) {
            cap.set(Videoio.CAP_PROP_CONVERT_RGB, 0);
            if (probeFrame(cap)) {
                logBackend(file, cap, "default");
                return cap;
            }
        }
        cap.release();
        return null;
    }

    static VideoCapture tryOpen(String file, int api, String label, double convertRgb) {
        VideoCapture cap = new VideoCapture();
        MatOfInt params = new MatOfInt(Videoio.CAP_PROP_CONVERT_RGB, (int) convertRgb);
        try {
            boolean opened = cap.open(file, api, params);
            if (!opened || !cap.isOpened()) {
                cap.release();
                params.release();
                return null;
            }
        } catch (Exception e) {
            cap.release();
            params.release();
            return null;
        }
        params.release();

        cap.set(Videoio.CAP_PROP_CONVERT_RGB, convertRgb);
        if (!probeFrame(cap)) {
            cap.release();
            return null;
        }
        logBackend(file, cap, label + " convertRGB=" + (int) convertRgb);
        return cap;
    }

    static boolean probeFrame(VideoCapture cap) {
        Mat probe = new Mat();
        boolean ok = cap.read(probe) && !probe.empty();
        if (ok) {
            Mat bgr = new Mat();
            ok = toBgr(probe, bgr);
            bgr.release();
        }
        probe.release();
        if (!ok) {
            return false;
        }
        cap.set(Videoio.CAP_PROP_POS_FRAMES, 0);
        cap.set(Videoio.CAP_PROP_POS_MSEC, 0);
        return true;
    }

    static void logBackend(String file, VideoCapture cap, String requested) {
        String backend = requested;
        try {
            String named = cap.getBackendName();
            if (named != null && !named.isEmpty()) {
                backend = named + " / " + requested;
            }
        } catch (Exception ignored) {
        }
        System.out.println("Opened " + file + " [" + backend + "]");
    }

    static boolean toBgr(Mat src, Mat dst) {
        if (src == null || src.empty()) {
            return false;
        }
        int type = src.type();
        if (type == CvType.CV_8UC3) {
            src.copyTo(dst);
            return true;
        }
        if (type == CvType.CV_8UC4) {
            Imgproc.cvtColor(src, dst, Imgproc.COLOR_BGRA2BGR);
            return !dst.empty();
        }
        if (type == CvType.CV_8UC2) {
            Imgproc.cvtColor(src, dst, Imgproc.COLOR_YUV2BGR_YUY2);
            return !dst.empty();
        }
        if (src.channels() == 1) {
            int h = src.rows();
            int w = src.cols();
            if (h * 2 % 3 == 0) {
                int visH = h * 2 / 3;
                if (visH > 0 && visH * 3 / 2 == h) {
                    try {
                        Imgproc.cvtColor(src, dst, Imgproc.COLOR_YUV2BGR_NV12);
                        if (!dst.empty() && dst.channels() == 3) {
                            return true;
                        }
                    } catch (Exception ignored) {
                    }
                }
            }
            Imgproc.cvtColor(src, dst, Imgproc.COLOR_GRAY2BGR);
            return !dst.empty();
        }
        src.copyTo(dst);
        return !dst.empty();
    }

    static boolean readBgr(VideoCapture cap, Mat bgr) {
        if (cap == null || !cap.isOpened()) {
            return false;
        }
        Mat raw = new Mat();
        if (!cap.read(raw) || raw.empty()) {
            raw.release();
            return false;
        }
        boolean ok = toBgr(raw, bgr);
        raw.release();
        return ok && bgr != null && !bgr.empty();
    }

    static boolean readOrLoop(VideoCapture[] caps, int i, Path path, Mat frame) {
        if (readBgr(caps[i], frame)) {
            return true;
        }

        caps[i].set(Videoio.CAP_PROP_POS_FRAMES, 0);
        caps[i].set(Videoio.CAP_PROP_POS_MSEC, 0);
        if (readBgr(caps[i], frame)) {
            System.out.println("Video " + i + " looped via seek");
            return true;
        }

        caps[i].release();
        caps[i] = openVideo(path);
        if (readBgr(caps[i], frame)) {
            System.out.println("Video " + i + " reopened after loop");
            return true;
        }

        System.err.println("Warning: Could not read frame from video " + i);
        return false;
    }

    static byte[] encodeJpeg(Mat frame) {
        MatOfByte buf    = new MatOfByte();
        MatOfInt  params = new MatOfInt(Imgcodecs.IMWRITE_JPEG_QUALITY, 88);
        Imgcodecs.imencode(".jpg", frame, buf, params);
        return buf.toArray();
    }

    static void writeFrame(OutputStream out, byte[] jpeg) throws IOException {
        String header = "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                      + jpeg.length + "\r\n\r\n";
        out.write(header.getBytes("UTF-8"));
        out.write(jpeg);
        out.write("\r\n".getBytes("UTF-8"));
        out.flush();
    }
}
