package com.middleware.panorama.wp5;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Customisation file for WP-5: everything that depends on the 3D model or the car
 * lives in one JSON file (see config/vehicle-profile.golf-r.json). No recompiling needed.
 *
 * Sections: model, vehicle, steering, zones, colors, bindings.
 */
public final class VehicleProfile {

    /** One "signal -> 3D node rotation" rule. */
    public static final class Binding {
        public String key, node, source;
        public double[] axis = {0, 1, 0};
        public double sign = 1, offsetDeg = 0, minDeg = -720, maxDeg = 720;
    }

    public final String name;
    public final String modelFile;
    public final List<Binding> bindings = new ArrayList<>();
    public final double[] forward, left, up;      // unit vectors: vehicle axes expressed in MODEL axes
    public final double[] origin;                 // rear-axle centre (ground level) in MODEL coordinates
    final Map<String, Object> numbers = new LinkedHashMap<>();   // flattened vehicle/steering/zones
    final Map<String, String> colors = new LinkedHashMap<>();
    public final String rawJson;

    // ---------------------------------------------------------------- loading

    /** Built-in profile: generic names. Android must still be told the real node names! */
    public static VehicleProfile defaults() {
        return fromJson(DEFAULT_JSON);
    }

    public static VehicleProfile load(Path file) throws IOException {
        return fromJson(new String(Files.readAllBytes(file), StandardCharsets.UTF_8));
    }

    @SuppressWarnings("unchecked")
    public static VehicleProfile fromJson(String json) {
        Object root = MiniJson.parse(json);
        if (!(root instanceof Map)) throw new IllegalArgumentException("profile must be a JSON object");
        return new VehicleProfile((Map<String, Object>) root, json);
    }

    @SuppressWarnings("unchecked")
    private VehicleProfile(Map<String, Object> r, String raw) {
        this.rawJson = raw;
        Map<String, Object> model = map(r.get("model"));
        this.name = str(r.get("profileName"), "unnamed");
        this.modelFile = str(model.get("file"), "");

        Map<String, Object> frame = map(model.get("frame"));
        forward = axis(str(frame.get("forward"), "+Z"));
        left    = axis(str(frame.get("left"), "+X"));
        up      = axis(str(frame.get("up"), "+Y"));
        origin  = vec3(model.get("rearAxleOriginM"), new double[]{0, 0, 0});

        for (String sec : new String[]{"vehicle", "steering", "zones"})
            for (Map.Entry<String, Object> e : map(r.get(sec)).entrySet())
                if (e.getValue() instanceof Double) numbers.put(e.getKey(), e.getValue());

        for (Map.Entry<String, Object> e : map(r.get("colors")).entrySet())
            colors.put(e.getKey().toUpperCase(), String.valueOf(e.getValue()));

        for (Map.Entry<String, Object> e : map(r.get("bindings")).entrySet()) {
            Map<String, Object> b = map(e.getValue());
            Binding x = new Binding();
            x.key = e.getKey();
            x.node = str(b.get("node"), e.getKey());
            x.source = str(b.get("source"), "");
            if (!(x.source.equals("steeringWheelDeg") || x.source.equals("leftWheelDeg")
                    || x.source.equals("rightWheelDeg") || x.source.equals("roadWheelAvgDeg")))
                throw new IllegalArgumentException("binding '" + x.key + "': unknown source '" + x.source
                        + "' (use steeringWheelDeg, leftWheelDeg, rightWheelDeg or roadWheelAvgDeg)");
            x.axis = vec3(b.get("axis"), new double[]{0, 1, 0});
            x.sign = dbl(b.get("sign"), 1);
            x.offsetDeg = dbl(b.get("offsetDeg"), 0);
            x.minDeg = dbl(b.get("minDeg"), -720);
            x.maxDeg = dbl(b.get("maxDeg"), 720);
            bindings.add(x);
        }
    }

    // ---------------------------------------------------------------- queries

    /** Number from vehicle/steering/zones, or null when the profile does not define it. */
    public Double number(String key) {
        Object v = numbers.get(key);
        return v instanceof Double ? (Double) v : null;
    }

    public String color(String zoneName) { return colors.get(zoneName.toUpperCase()); }

    /** Vehicle-frame point (x fwd, y left, z up; metres) -> MODEL coordinates. */
    public double[] toModel(double xv, double yv, double zv) {
        double[] p = new double[3];
        for (int k = 0; k < 3; k++) p[k] = origin[k] + xv * forward[k] + yv * left[k] + zv * up[k];
        return p;
    }

    // ---------------------------------------------------------------- helpers

    @SuppressWarnings("unchecked")
    private static Map<String, Object> map(Object o) {
        return o instanceof Map ? (Map<String, Object>) o : new LinkedHashMap<>();
    }
    private static String str(Object o, String def) { return o instanceof String ? (String) o : def; }
    private static double dbl(Object o, double def) { return o instanceof Double ? (Double) o : def; }
    private static double[] vec3(Object o, double[] def) {
        if (!(o instanceof List) || ((List<?>) o).size() != 3) return def;
        double[] v = new double[3];
        for (int k = 0; k < 3; k++) {
            Object x = ((List<?>) o).get(k);
            if (!(x instanceof Double)) return def;
            v[k] = (Double) x;
        }
        return v;
    }
    /** "+Z" / "-X" / "Y" -> unit vector. */
    static double[] axis(String a) {
        double sg = a.startsWith("-") ? -1 : 1;
        String c = a.replace("+", "").replace("-", "").trim().toUpperCase();
        switch (c) {
            case "X": return new double[]{sg, 0, 0};
            case "Y": return new double[]{0, sg, 0};
            case "Z": return new double[]{0, 0, sg};
            default: throw new IllegalArgumentException("bad axis '" + a + "' (use +X -X +Y -Y +Z -Z)");
        }
    }

    // Generic fallback. NODE NAMES ARE PLACEHOLDERS — replace with names from your .glb.
    private static final String DEFAULT_JSON = "{"
        + "\"profileName\":\"default-generic\","
        + "\"model\":{\"file\":\"\",\"frame\":{\"forward\":\"+Z\",\"left\":\"+X\",\"up\":\"+Y\"},\"rearAxleOriginM\":[0,0,0]},"
        + "\"bindings\":{"
        + "\"steeringWheel\":{\"node\":\"steering_wheel\",\"source\":\"steeringWheelDeg\",\"axis\":[0,0,-1],\"sign\":1},"
        + "\"wheelFrontLeft\":{\"node\":\"wheel_front_left_steer\",\"source\":\"leftWheelDeg\",\"axis\":[0,1,0],\"sign\":1},"
        + "\"wheelFrontRight\":{\"node\":\"wheel_front_right_steer\",\"source\":\"rightWheelDeg\",\"axis\":[0,1,0],\"sign\":1}"
        + "}}";
}
