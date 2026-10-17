package com.middleware.panorama.wp5;

import com.middleware.panorama.StitchingCore;
import org.opencv.core.Mat;
import org.opencv.core.Rect;

import java.util.Set;

/**
 * Curb detection on the STITCHED 360 degree PANORAMA picture (the same picture the driver / the app sees).
 *
 * The panorama is cut into its camera sections (front | right | rear | left, the same x positions that the stitcher used),
 * every section is analysed (segmentation model or classic edge detector, see CurbVision), and the curb found in a
 * section belongs to that section's side of the car. Lines are reported in the pixels of that section, which is what
 * CurbModel converts back to PANORAMA pixels, so the highlight lines up with the panorama picture exactly.
 */
public final class CurbPanorama {
    private final String[] cameras;
    private final int camW, overlap;
    private final boolean[] analysed;
    private final long[] dbgN;

    public CurbPanorama(String[] cameras, int camW, int overlapPx, Set<String> sidesToAnalyse) {
        this.cameras = cameras; this.camW = camW;
        this.overlap = StitchingCore.clampOverlap(overlapPx, camW);
        this.analysed = new boolean[cameras.length];
        this.dbgN = new long[cameras.length];
        for (int i = 0; i < cameras.length; i++) analysed[i] = sidesToAnalyse.contains(cameras[i]);
    }

    public boolean analyses(int cameraIndex) { return analysed[cameraIndex]; }

    /** One candidate per camera section (null = no curb in that section of the panorama). */
    CurbVision.Raw[] analyse(Mat panorama) {
        CurbVision.Raw[] out = new CurbVision.Raw[cameras.length];
        int H = panorama.rows(), W = panorama.cols();
        for (int i = 0; i < cameras.length; i++) {
            if (!analysed[i]) continue;
            int x0 = StitchingCore.cameraX(i, camW, overlap);
            int w = Math.min(camW, W - x0);
            if (w < camW / 2) continue;                       // panorama narrower than expected
            // The neighbouring cameras overlap by 'overlap' px (blended). Every part of the panorama must belong to ONE side,
            // otherwise a curb at a seam would be reported twice: a section owns the columns up to the middle of its overlaps,
            // and a curb counts for the section in which the CENTRE of its line lies.
            int lo = i > 0 ? overlap / 2 : 0;
            int hi = w - (i < cameras.length - 1 ? overlap / 2 : 0);
            Mat section = panorama.submat(new Rect(x0, 0, w, H));
            try {
                CurbVision.SEAMS.set(new int[]{i > 0 ? overlap : 0, i < cameras.length - 1 ? overlap : 0});   // ignore the blended seams
                CurbVision.Raw r = CurbVision.detectAny(section, cameras[i]);
                if (r != null) {
                    double cx = (r.line[0] + r.line[2]) / 2.0;
                    if (cx >= lo && cx < hi) {
                        out[i] = r;                                              // otherwise the neighbouring section owns it
                        // -Dwp5.vision.debug=true : saves the section with the found line drawn in red + prints what it was based on
                        if (CurbVision.DEBUG && (dbgN[i]++ % 20 == 0)) CurbVision.debugDump(section, r, cameras[i], dbgN[i]);
                    }
                }
            }
            finally { CurbVision.SEAMS.remove(); section.release(); }
        }
        return out;
    }
}
