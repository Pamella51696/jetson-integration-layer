import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;
 
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executors;
 
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
 
    // =========================================================================
    public static void main(String[] args) throws IOException {
 
        Path frontVideo = ensureDecodable(Paths.get("src", "main", "java", "2.mp4"));
        Path rearVideo  = ensureDecodable(Paths.get("src", "main", "java", "3.mp4"));
        Path sideVideo  = ensureDecodable(Paths.get("src", "main", "java", "4.mp4"));
        Path backVideo  = ensureDecodable(Paths.get("src", "main", "java", "1.mp4"));
 
        Path[] videos = { frontVideo, rearVideo, sideVideo, backVideo };
 
        for (Path v : videos) {
            if (!Files.exists(v) || Files.isDirectory(v)) {
                System.err.println("Video file not found: " + v);
                return;
            }
        }
 
        int port = args.length > 0 ? Integer.parseInt(args[0]) : DEFAULT_PORT;
 
        try {
            System.loadLibrary(Core.NATIVE_LIBRARY_NAME);
        } catch (UnsatisfiedLinkError e) {
            System.err.println("OpenCV native library not found: " + e.getMessage());
            return;
        }
 
        HttpServer server = HttpServer.create(new InetSocketAddress(port), 0);
 
        server.createContext("/stitch", new StitchHandler(videos));
        server.createContext("/play",   new PlayerPageHandler());
 
        server.setExecutor(Executors.newFixedThreadPool(4));
        server.start();
 
        System.out.println("Server started  →  http://localhost:" + port + "/play");
    }
 
    // STITCH HANDLER  —  feather-blend panorama only

    private static class StitchHandler implements HttpHandler {
      private final Path[] videoFiles;
      StitchHandler(Path[] f) { this.videoFiles = f; }
      private int resetCount = 0;

      @Override public void handle(HttpExchange ex) throws IOException {
        if (!"GET".equalsIgnoreCase(ex.getRequestMethod())) {
          ex.sendResponseHeaders(405, -1); return;
        }

        VideoCapture[] caps = new VideoCapture[videoFiles.length];
        for (int i = 0; i < videoFiles.length; i++) {
          caps[i] = new VideoCapture(videoFiles[i].toString());
          if (!caps[i].isOpened()) {
            ex.sendResponseHeaders(500, -1); return;
          }
        }

        ex.getResponseHeaders().set("Content-Type", "multipart/x-mixed-replace; boundary=frame");
        ex.sendResponseHeaders(200, 0);

        try (OutputStream out = ex.getResponseBody()) {
          Mat[] frames  = new Mat[videoFiles.length];
          Mat[] resized = new Mat[videoFiles.length];
          for (int i = 0; i < videoFiles.length; i++) {
            frames[i]  = new Mat();
            resized[i] = new Mat();
          }

          while (true) {
            for (int i = 0; i < caps.length; i++) {
              boolean frameRead = caps[i].read(frames[i]);
              if (!frameRead || frames[i].empty()) {
                // Video ended - close and reopen from start
                caps[i].release();
                caps[i] = new VideoCapture(videoFiles[i].toString());
                
                if (!caps[i].isOpened()) {
                  System.err.println("Error: Could not reopen video " + i);
                  continue;
                }
                
                frameRead = caps[i].read(frames[i]);
                if (!frameRead || frames[i].empty()) {
                  System.err.println("Warning: Could not read frame from video " + i + " after reopening");
                  continue;
                }
                resetCount++;
                System.out.println("Video " + i + " restarted (total resets: " + resetCount + ")");
              }
              Imgproc.resize(frames[i], resized[i], new Size(TARGET_WIDTH, TARGET_HEIGHT));
            }

            Mat panorama = featherStitch(resized);
            writeFrame(out, encodeJpeg(panorama));
            panorama.release();
          }
        } 
        finally {
          for (VideoCapture c : caps) c.release();
        }

      } // handle()
    } // StichHandler
 

    // PLAYER PAGE  —  stitched panorama only, full-viewport
 
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
 
    
    // SHARED HELPERS
   
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
 


