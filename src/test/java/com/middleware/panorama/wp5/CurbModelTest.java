package com.middleware.panorama.wp5;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/** Pure-Java tests for Package 2: curb zones from the camera frames (left / right / front / rear). */
class CurbModelTest {

    @BeforeAll static void defaultsOnly() { System.setProperty("wp5.profile", "none"); }

    private static final String[] CAMS = {"front", "right", "rear", "left"};   // panel order of the server
    private final Wp5Config cfg = Wp5Config.load();
    private CurbModel model() { return new CurbModel(cfg, CAMS, 640, 360, 80); }

    private static void curb(CurbModel m, String side, double d, long now) {
        m.ingestVision(side, true, d, 0.9, null, side.toLowerCase(), "CAMERA", now);
    }

    @Test
    void classify_thresholds() {
        CurbModel m = model();
        assertEquals(Zone.DANGER,  m.classify(0.30, Zone.NONE));
        assertEquals(Zone.DANGER,  m.classify(cfg.dangerM, Zone.NONE));
        assertEquals(Zone.WARNING, m.classify(0.80, Zone.NONE));
        assertEquals(Zone.SAFE,    m.classify(1.50, Zone.NONE));
        assertEquals(Zone.SAFE,    m.classify(cfg.maxRangeM, Zone.NONE));
    }

    @Test
    void classify_hysteresis_preventsFlicker() {
        CurbModel m = model();
        assertEquals(Zone.DANGER, m.classify(cfg.dangerM + cfg.hysteresisM / 2, Zone.DANGER));
        assertEquals(Zone.WARNING, m.classify(cfg.dangerM + cfg.hysteresisM + 0.01, Zone.DANGER));
        assertEquals(Zone.DANGER, m.classify(cfg.dangerM - 0.01, Zone.WARNING));
    }

    @Test
    void unknownSide_isIgnored() {
        CurbModel m = model();
        curb(m, "TOP", 0.3, 0);
        assertTrue(m.snapshot(1, 0).json.contains("\"zones\":[]"));
    }

    @Test
    void noCurb_noZone_forAllFourSides() {
        CurbModel m = model();
        for (String s : CurbModel.SIDES) m.ingestVision(s, false, Double.NaN, 0, null, s.toLowerCase(), "CAMERA", 0);
        String j = m.snapshot(1, 0).json;
        assertTrue(j.contains("\"zones\":[]"), j);
        assertTrue(j.contains("\"worstZone\":\"NONE\""), j);
    }

    @Test
    void everySide_getsItsOwnCameraStrip() {
        CurbModel m = model();
        for (String s : CurbModel.SIDES) curb(m, s, 0.3, 1000);
        String j = m.snapshot(1, 1000).json;
        // strip x = index*(640-80): front 0, right 560, rear 1120, left 1680; width = 640
        assertTrue(j.contains("\"pos\":\"FRONT\""), j);
        assertTrue(j.contains("\"camera\":\"front\"") && j.contains("\"pano\":{\"x\":0,\"w\":640"), j);
        assertTrue(j.contains("\"camera\":\"rear\"") && j.contains("\"pano\":{\"x\":1120,\"w\":640"), j);
        assertTrue(j.contains("\"camera\":\"left\"") && j.contains("\"pano\":{\"x\":1680,\"w\":640"), j);
        assertTrue(j.contains("\"camera\":\"right\"") && j.contains("\"pano\":{\"x\":560,\"w\":640"), j);
        assertTrue(j.contains("\"panorama\":{\"width\":2320,\"height\":360}"), j);
    }

    @Test
    void summary_hasLeftRightFrontRearAndNearest() {
        CurbModel m = model();
        curb(m, "LEFT", 0.3, 0);
        curb(m, "REAR", 0.8, 0);
        curb(m, "FRONT", 1.8, 0);
        String j = m.snapshot(1, 0).json;
        assertTrue(j.contains("\"leftWorst\":\"DANGER\""), j);
        assertTrue(j.contains("\"rearWorst\":\"WARNING\""), j);
        assertTrue(j.contains("\"frontWorst\":\"SAFE\""), j);
        assertTrue(j.contains("\"rightWorst\":\"NONE\""), j);
        assertTrue(j.contains("\"nearestZoneId\":\"CURB_LEFT\""), j);
        assertTrue(j.contains("\"worstZone\":\"DANGER\""), j);
    }

    @Test
    void zoneWithoutRefresh_disappears_notGrey() {
        CurbModel m = model();
        curb(m, "FRONT", 0.3, 0);
        assertTrue(m.snapshot(1, 500).json.contains("CURB_FRONT"));
        assertTrue(m.snapshot(2, 5000).json.contains("\"zones\":[]"));
    }

    @Test
    void curbLinePixels_areMovedIntoPanoramaCoordinates() {
        CurbModel m = model();
        m.ingestVision("left", true, 0.4, 0.9, new double[]{20, 267, 620, 267}, "left", "CAMERA", 0);
        assertTrue(m.snapshot(1, 0).json.contains("\"x1\":1700"));      // left strip starts at 3*(640-80)=1680
    }

    @Test
    void signature_changesOnlyWhenVisibleStateChanges() {
        CurbModel m = model();
        curb(m, "REAR", 1.500, 0);
        String s1 = m.snapshot(1, 0).signature;
        curb(m, "REAR", 1.505, 10);                       // < 2 cm change
        assertEquals(s1, m.snapshot(2, 10).signature);
        curb(m, "REAR", 0.400, 20);
        assertNotEquals(s1, m.snapshot(3, 20).signature);
    }

    @Test
    void handMadeTestInput_beatsDetectorFor1500ms() {
        CurbModel m = model();
        m.ingestVisionJson("{\"sides\":[{\"side\":\"front\",\"detected\":true,\"distanceM\":0.7}]}", 1000);
        m.ingestVision("front", false, Double.NaN, 0, null, "front", "CAMERA", 1100);   // detector says "no curb" -> ignored
        assertTrue(m.snapshot(1, 1200).json.contains("\"frontWorst\":\"WARNING\""));
        m.ingestVision("front", false, Double.NaN, 0, null, "front", "CAMERA", 2600);   // lock expired -> detector wins
        assertTrue(m.snapshot(2, 2600).json.contains("\"zones\":[]"));
    }

    @Test
    void zoneWorst_ignoresNone() {
        assertEquals(Zone.SAFE, Zone.worst(Zone.NONE, Zone.SAFE));
        assertEquals(Zone.DANGER, Zone.worst(Zone.WARNING, Zone.DANGER));
    }
}
