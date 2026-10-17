package com.middleware.panorama.wp5;

import org.opencv.core.Mat;

import java.util.Set;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Analyses the stitched PANORAMA on a background thread, so the video never waits for the detector.
 * The video loop calls submit(panorama) now and then; only the NEWEST picture is kept (older ones are dropped).
 * For every camera section the result is smoothed over several pictures (CurbVision.Tracker) and handed to the sink.
 */
public final class CurbVisionWorker {

    public interface Sink { void accept(String camera, CurbVision.Result result); }

    private final AtomicReference<Mat> slot = new AtomicReference<>();
    private final CurbPanorama panorama;
    private final CurbVision.Tracker[] trackers;
    private final String[] cameras;
    private final Sink sink;

    public CurbVisionWorker(String[] cameras, int camW, int overlapPx, Set<String> sides, Sink sink) {
        this.cameras = cameras; this.sink = sink;
        this.panorama = new CurbPanorama(cameras, camW, overlapPx, sides);
        this.trackers = new CurbVision.Tracker[cameras.length];
        for (int i = 0; i < cameras.length; i++) if (panorama.analyses(i)) trackers[i] = new CurbVision.Tracker(cameras[i]);
        Thread t = new Thread(this::loop, "wp5-curb-vision");
        t.setDaemon(true);
        t.start();
    }

    /** Hand over the stitched panorama (it is copied, the caller may release its Mat). */
    public void submit(Mat stitchedPanorama) {
        if (stitchedPanorama == null || stitchedPanorama.empty()) return;
        Mat old = slot.getAndSet(stitchedPanorama.clone());
        if (old != null) old.release();
    }

    private void loop() {
        while (!Thread.currentThread().isInterrupted()) {
            Mat pano = slot.getAndSet(null);
            if (pano == null) { try { Thread.sleep(10); } catch (InterruptedException e) { return; } continue; }
            try {
                CurbVision.Raw[] found = panorama.analyse(pano);
                for (int i = 0; i < cameras.length; i++)
                    if (trackers[i] != null) sink.accept(cameras[i], trackers[i].updateRaw(found[i], pano.rows()));
            } catch (RuntimeException e) {
                System.err.println("[WP5] vision error: " + e);
            } finally { pano.release(); }
        }
    }
}
