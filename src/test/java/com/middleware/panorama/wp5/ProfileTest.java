package com.middleware.panorama.wp5;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/** Customisation: JSON parser, vehicle-profile.json, 3D transforms, panorama map and colours. */
class ProfileTest {

    @BeforeEach void before() { System.setProperty("wp5.profile", "none"); }
    @AfterEach  void after()  { System.setProperty("wp5.profile", "none"); }

    private static final String CUSTOM = "{"
        + "\"profileName\":\"unit\","
        + "\"model\":{\"file\":\"car.glb\",\"frame\":{\"forward\":\"-Z\",\"left\":\"-X\",\"up\":\"+Y\"},\"rearAxleOriginM\":[0,0.1,1.5]},"
        + "\"vehicle\":{\"wheelbaseM\":3.0,\"steeringRatio\":20},"
        + "\"zones\":{\"dangerM\":0.40,\"warningM\":0.90},"
        + "\"colors\":{\"DANGER\":\"#CC0000\"},"
        + "\"bindings\":{"
        + "  \"steeringWheel\":{\"node\":\"Lenkrad\",\"source\":\"steeringWheelDeg\",\"axis\":[0,0,-1],\"sign\":-1,\"offsetDeg\":10,\"minDeg\":-100,\"maxDeg\":100},"
        + "  \"flSteer\":{\"node\":\"FL_pivot\",\"source\":\"leftWheelDeg\",\"axis\":[0,1,0]}"
        + "}"
        + "}";

    private Wp5Config withProfile(String json) throws Exception {
        Path f = Files.createTempFile("wp5profile", ".json");
        Files.write(f, json.getBytes("UTF-8"));
        System.setProperty("wp5.profile", f.toString());
        return Wp5Config.load();
    }

    // ---------------------------------------------------------------- MiniJson

    @Test @SuppressWarnings("unchecked")
    void miniJson_parsesNestedStructures() {
        Map<String, Object> m = (Map<String, Object>) MiniJson.parse(
            "{\"a\":1.5,\"b\":[1,2,{\"c\":\"x\\ny\"}],\"d\":true,\"e\":null,\"f\":-2e1}");
        assertEquals(1.5, m.get("a"));
        assertEquals(true, m.get("d"));
        assertNull(m.get("e"));
        assertEquals(-20.0, m.get("f"));
        List<Object> b = (List<Object>) m.get("b");
        assertEquals("x\ny", ((Map<String, Object>) b.get(2)).get("c"));
    }

    @Test
    void miniJson_acceptsUtf8Bom_andReportsLineOfErrors() {
        assertNotNull(MiniJson.parse("\uFEFF{\"a\":1}"));
        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
            () -> MiniJson.parse("{\n  \"a\": 1,\n  \"b\": }\n"));
        assertTrue(e.getMessage().contains("line 3"), e.getMessage());
    }

    // ---------------------------------------------------------------- defaults

    @Test
    void defaults_haveThreeBindings_withUpAxisForWheels() {
        VehicleProfile p = VehicleProfile.defaults();
        assertEquals(3, p.bindings.size());
        assertArrayEquals(new double[]{0, 1, 0}, p.bindings.get(1).axis);
    }

    // ---------------------------------------------------------------- custom profile

    @Test
    void customProfile_overridesNumbers_butSystemPropertyWins() throws Exception {
        Wp5Config c = withProfile(CUSTOM);
        assertEquals(3.0, c.wheelbaseM, 1e-9);
        assertEquals(0.40, c.dangerM, 1e-9);
        assertEquals(1.60, c.trackWidthM, 1e-9);            // not in profile -> built-in default
        System.setProperty("wp5.dangerM", "0.30");
        try { assertEquals(0.30, Wp5Config.load().dangerM, 1e-9); }
        finally { System.clearProperty("wp5.dangerM"); }
    }

    @Test
    void customProfile_transformUsesSignOffsetAndClamp() throws Exception {
        Wp5Config c = withProfile(CUSTOM);
        SteeringModel m = new SteeringModel(c);
        String j = m.compute(1, 0, 50, "D", 0).toJson();
        // sign -1, offset +10:  -50 + 10 = -40
        assertTrue(j.contains("\"node\":\"Lenkrad\""), j);
        assertTrue(j.contains("\"angleDeg\":-40.000"), j);
        // clamp to minDeg -100 : steering 300 -> -300+10 = -290 -> -100
        assertTrue(m.compute(2, 0, 300, "D", 0).toJson().contains("\"angleDeg\":-100.000"));
    }

    @Test
    void quaternion_matchesAxisAngle() {
        double[] q = SteeringModel.quat(new double[]{0, 1, 0}, 90);
        assertEquals(0, q[0], 1e-9);
        assertEquals(Math.sqrt(0.5), q[1], 1e-9);
        assertEquals(0, q[2], 1e-9);
        assertEquals(Math.sqrt(0.5), q[3], 1e-9);
        double[] id = SteeringModel.quat(new double[]{0, 0, -1}, 0);
        assertEquals(1.0, id[3], 1e-9);                       // zero angle = identity
        double[] n = SteeringModel.quat(new double[]{0, 5, 0}, 90);   // axis is normalised
        assertEquals(Math.sqrt(0.5), n[1], 1e-9);
    }

    @Test
    void guideLines_areConvertedIntoModelCoordinates() throws Exception {
        // forward = -Z, left = -X, origin (0,0.1,1.5): vehicle point (2, 1) -> (-1, 0.1, 1.5-2)
        VehicleProfile p = VehicleProfile.fromJson(CUSTOM);
        double[] m = p.toModel(2, 1, 0);
        assertArrayEquals(new double[]{-1.0, 0.1, -0.5}, m, 1e-9);
    }

    @Test
    void steeringJson_containsModelBlock_andIsValidJson() throws Exception {
        Wp5Config c = withProfile(CUSTOM);
        String j = new SteeringModel(c).compute(5, 1, 120, "R", 1).toJson();
        Object parsed = MiniJson.parse(j);                    // must parse with our strict parser
        assertTrue(parsed instanceof Map);
        assertTrue(j.contains("\"model\":{\"profile\":\"unit\",\"modelFile\":\"car.glb\""));
        assertTrue(j.contains("\"quatOrder\":\"xyzw\""));
        assertTrue(j.contains("\"key\":\"flSteer\""));
    }

    @Test
    void curb_usesProfileColors() throws Exception {
        Wp5Config c = withProfile(CUSTOM);
        CurbModel cm = new CurbModel(c, new String[]{"front", "rear", "left", "right"}, 640, 360, 80);
        cm.ingestVision("rear", true, 0.2, 0.9, null, "rear", "CAMERA", 0);
        String j = cm.snapshot(1, 0).json;
        assertTrue(j.contains("\"color\":\"#CC0000\""), j);        // custom danger colour
        assertTrue(j.contains("\"camera\":\"rear\""), j);
    }

    @Test
    void badSource_isRejectedWithClearMessage() {
        String bad = "{\"bindings\":{\"x\":{\"node\":\"n\",\"source\":\"bananas\"}}}";
        IllegalArgumentException e = assertThrows(IllegalArgumentException.class, () -> VehicleProfile.fromJson(bad));
        assertTrue(e.getMessage().contains("bananas"));
    }

    @Test
    void brokenProfileFile_fallsBackToDefaults_notCrash() throws Exception {
        Wp5Config c = withProfile("{ this is not json");
        assertEquals("default-generic", c.profile.name);
    }
}
