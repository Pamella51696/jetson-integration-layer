package com.middleware.panorama.wp5;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/** Pure-Java tests for Package 1 (steering + wheel visualization). No OpenCV needed. */
class SteeringModelTest {

    @BeforeAll static void defaultsOnly() { System.setProperty("wp5.profile", "none"); }

    private final Wp5Config cfg = Wp5Config.load();
    private final SteeringModel model = new SteeringModel(cfg);

    @Test
    void straight_hasZeroWheelAnglesAndStraightGuides() {
        SteeringState s = model.compute(1, 0, 0, "D", 0);
        assertEquals("STRAIGHT", s.direction);
        assertEquals(0, s.leftWheelDeg, 1e-9);
        assertEquals(0, s.rightWheelDeg, 1e-9);
        assertEquals(0, s.turnRadiusM, 1e-9);
        double[] last = s.guideLeft[s.guideLeft.length - 1];
        assertEquals(cfg.pathLengthM, last[0], 1e-6);                 // goes forward
        assertEquals(cfg.vehicleWidthM / 2, last[1], 1e-6);           // left edge stays at +w/2
    }

    @Test
    void positiveAngle_isLeftTurn_innerWheelTurnsMore() {
        SteeringState s = model.compute(1, 0, 300, "D", 0);
        assertEquals("LEFT", s.direction);
        assertTrue(s.leftWheelDeg > s.rightWheelDeg, "left is the inner wheel");
        assertTrue(s.rightWheelDeg > 0);
        assertEquals(300 / cfg.steeringRatio, s.roadWheelAvgDeg, 1e-9);
        assertTrue(s.curvature > 0);
    }

    @Test
    void negativeAngle_isRightTurn_mirrorsLeft() {
        SteeringState l = model.compute(1, 0, 200, "D", 0);
        SteeringState r = model.compute(1, 0, -200, "D", 0);
        assertEquals("RIGHT", r.direction);
        assertEquals(l.leftWheelDeg, -r.rightWheelDeg, 1e-9);
        assertEquals(l.rightWheelDeg, -r.leftWheelDeg, 1e-9);
    }

    @Test
    void ackermann_matchesTurnRadiusGeometry() {
        SteeringState s = model.compute(1, 0, 270, "D", 0);
        double R = s.turnRadiusM;
        double expectedInner = Math.toDegrees(Math.atan(cfg.wheelbaseM / (R - cfg.trackWidthM / 2)));
        double expectedOuter = Math.toDegrees(Math.atan(cfg.wheelbaseM / (R + cfg.trackWidthM / 2)));
        assertEquals(expectedInner, s.leftWheelDeg, 1e-6);
        assertEquals(expectedOuter, s.rightWheelDeg, 1e-6);
    }

    @Test
    void reverse_guideLinesGoBackwards() {
        SteeringState s = model.compute(1, 0, 150, "R", 1);
        assertEquals("R", s.gear);
        assertTrue(s.guideLeft[s.guideLeft.length - 1][0] < 0);
        assertTrue(s.guideRight[s.guideRight.length - 1][0] < 0);
    }

    @Test
    void leftTurn_pathCurvesToTheLeft() {
        SteeringState s = model.compute(1, 0, 400, "D", 0);
        assertTrue(s.guideLeft[s.guideLeft.length - 1][1] > cfg.vehicleWidthM / 2);
    }

    @Test
    void clampsToMaxSteeringWheelAngle_andIgnoresNaN() {
        assertEquals(cfg.maxSteeringWheelDeg, model.compute(1, 0, 99999, "D", 0).steeringWheelDeg, 1e-9);
        assertEquals(-cfg.maxSteeringWheelDeg, model.compute(1, 0, -99999, "D", 0).steeringWheelDeg, 1e-9);
        assertEquals(0, model.compute(1, 0, Double.NaN, "D", 0).steeringWheelDeg, 1e-9);
    }

    @Test
    void shouldPublish_deadbandGearAndHeartbeat() {
        SteeringState a = model.compute(1, 1000, 100.0, "D", 0);
        assertTrue(model.shouldPublish(null, a, 1000));
        assertFalse(model.shouldPublish(a, model.compute(2, 1050, 100.2, "D", 0), 1050)); // < deadband
        assertTrue(model.shouldPublish(a, model.compute(2, 1050, 101.0, "D", 0), 1050));  // >= deadband
        assertTrue(model.shouldPublish(a, model.compute(2, 1050, 100.0, "R", 0), 1050));  // gear changed
        assertTrue(model.shouldPublish(a, model.compute(2, 1000 + cfg.heartbeatMs, 100.1, "D", 0),
                1000 + cfg.heartbeatMs));                                                    // heartbeat
    }

    @Test
    void json_isWellFormedAndLocaleIndependent() {
        java.util.Locale old = java.util.Locale.getDefault();
        try {
            java.util.Locale.setDefault(java.util.Locale.GERMANY); // would print "1,5" with %f
            String j = model.compute(7, 123, 12.5, "R", 1.5).toJson();
            assertTrue(j.contains("\"type\":\"steering\""));
            assertTrue(j.contains("\"seq\":7"));
            assertTrue(j.contains("\"steeringWheelDeg\":12.500"));
            assertTrue(j.contains("\"valid\":true"));
            assertFalse(j.contains("12,5"));
        } finally { java.util.Locale.setDefault(old); }
    }

    @Test
    void reissue_keepsValuesButMarksStale() {
        SteeringState a = model.compute(1, 0, 50, "D", 0);
        SteeringState b = a.reissue(2, 5000, false);
        assertEquals(a.steeringWheelDeg, b.steeringWheelDeg, 1e-9);
        assertTrue(b.toJson().contains("\"valid\":false"));
    }
}
