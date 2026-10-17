package com.middleware.panorama.wp5;

/**
 * WP-5 / Package 1 — turns ONE raw number (steering-wheel angle from the
 * vehicle bus) into everything the Android 3D overlay needs:
 * steering-wheel rotation, per-wheel Ackermann steer angles, and the
 * predicted parking guide lines. Pure Java, no OpenCV, fully unit-testable.
 */
public final class SteeringModel {
    private final Wp5Config cfg;

    public SteeringModel(Wp5Config cfg) { this.cfg = cfg; }

    public SteeringState compute(long seq, long tsMs, double steeringWheelDegIn,
                                 String gearIn, double speedKph) {
        double swa = clamp(steeringWheelDegIn, -cfg.maxSteeringWheelDeg, cfg.maxSteeringWheelDeg);
        String gear = normGear(gearIn);

        // Bicycle model: average road-wheel angle and centre curvature
        double deltaDeg = swa / cfg.steeringRatio;
        double deltaRad = Math.toRadians(deltaDeg);
        double k = Math.tan(deltaRad) / cfg.wheelbaseM;          // 1/m, + = left
        boolean straight = Math.abs(k) < 1e-4;                   // radius > 10 km
        if (straight) k = 0;

        // Ackermann: inner wheel turns more than outer wheel
        double half = cfg.trackWidthM / 2.0;
        double leftDeg  = Math.toDegrees(Math.atan2(k * cfg.wheelbaseM, 1.0 - k * half));
        double rightDeg = Math.toDegrees(Math.atan2(k * cfg.wheelbaseM, 1.0 + k * half));

        String dir = straight ? "STRAIGHT" : (k > 0 ? "LEFT" : "RIGHT");
        double radius = straight ? 0 : 1.0 / Math.abs(k);

        // Guide lines: reverse -> path goes backwards
        double sign = "R".equals(gear) ? -1.0 : 1.0;
        double edge = cfg.vehicleWidthM / 2.0;
        double[][] left  = path(k, sign, +edge);
        double[][] right = path(k, sign, -edge);

        String modelJson = buildModelJson(swa, deltaDeg, leftDeg, rightDeg, left, right);
        return new SteeringState(seq, tsMs, swa, deltaDeg, leftDeg, rightDeg, k, radius,
                dir, gear, speedKph, left, right, modelJson, true);
    }

    // ------------------------------------------------------------------
    //  3D-model block: everything Android needs, already in MODEL terms
    // ------------------------------------------------------------------

    /**
     * For every binding of the profile:  angle = clamp(sign * value + offsetDeg, min, max),
     * and the same rotation as a quaternion [x,y,z,w] about the binding's axis.
     * Android applies it as:  node.localRotation = restRotation * quat  (rest = pose stored in the .glb).
     * Guide lines are also converted from vehicle frame to MODEL coordinates [x,y,z] (z = ground).
     */
    String buildModelJson(double swa, double roadAvg, double leftDeg, double rightDeg,
                          double[][] guideL, double[][] guideR) {
        VehicleProfile p = cfg.profile;
        StringBuilder sb = new StringBuilder(1024);
        sb.append("{\"profile\":").append(Json.str(p.name))
          .append(",\"modelFile\":").append(Json.str(p.modelFile))
          .append(",\"units\":\"deg\",\"quatOrder\":\"xyzw\",\"transforms\":[");
        boolean first = true;
        for (VehicleProfile.Binding b : p.bindings) {
            double v;
            switch (b.source) {
                case "steeringWheelDeg": v = swa; break;
                case "leftWheelDeg":     v = leftDeg; break;
                case "rightWheelDeg":    v = rightDeg; break;
                default:                 v = roadAvg;
            }
            double ang = clamp(b.sign * v + b.offsetDeg, b.minDeg, b.maxDeg);
            double[] q = quat(b.axis, ang);
            if (!first) sb.append(','); first = false;
            sb.append("{\"key\":").append(Json.str(b.key))
              .append(",\"node\":").append(Json.str(b.node))
              .append(",\"axis\":[").append(Json.num(b.axis[0])).append(',').append(Json.num(b.axis[1]))
              .append(',').append(Json.num(b.axis[2])).append(']')
              .append(",\"angleDeg\":").append(Json.num(ang))
              .append(",\"angleRad\":").append(Json.num(Math.toRadians(ang)))
              .append(",\"quat\":[").append(Json.num(q[0])).append(',').append(Json.num(q[1])).append(',')
              .append(Json.num(q[2])).append(',').append(Json.num(q[3])).append("]}");
        }
        sb.append("],\"guideLeft\":").append(modelPts(p, guideL))
          .append(",\"guideRight\":").append(modelPts(p, guideR)).append('}');
        return sb.toString();
    }

    private static String modelPts(VehicleProfile p, double[][] pts) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < pts.length; i++) {
            double[] m = p.toModel(pts[i][0], pts[i][1], 0);
            if (i > 0) sb.append(',');
            sb.append('[').append(Json.num(m[0])).append(',').append(Json.num(m[1])).append(',').append(Json.num(m[2])).append(']');
        }
        return sb.append(']').toString();
    }

    /** Axis-angle (degrees) -> quaternion [x,y,z,w]. Axis need not be normalised. */
    static double[] quat(double[] axis, double deg) {
        double n = Math.sqrt(axis[0] * axis[0] + axis[1] * axis[1] + axis[2] * axis[2]);
        double[] a = n < 1e-9 ? new double[]{0, 1, 0} : new double[]{axis[0] / n, axis[1] / n, axis[2] / n};
        double h = Math.toRadians(deg) / 2.0, s = Math.sin(h);
        return new double[]{a[0] * s, a[1] * s, a[2] * s, Math.cos(h)};
    }

    /** Points of a path offset laterally by 'offset' m (+ = left) from the rear-axle centre path. */
    double[][] path(double k, double sign, double offset) {
        int n = Math.max(2, cfg.pathPoints);
        double[][] pts = new double[n][2];
        for (int i = 0; i < n; i++) {
            double s = sign * cfg.pathLengthM * i / (n - 1);
            double x, y, theta;
            if (k == 0) { x = s; y = 0; theta = 0; }
            else { theta = k * s; x = Math.sin(theta) / k; y = (1 - Math.cos(theta)) / k; }
            pts[i][0] = x - offset * Math.sin(theta);
            pts[i][1] = y + offset * Math.cos(theta);
        }
        return pts;
    }

    /** Publish only when it matters: >= deadband change, gear change, or heartbeat due. */
    public boolean shouldPublish(SteeringState prev, SteeringState next, long nowMs) {
        if (prev == null) return true;
        if (!prev.gear.equals(next.gear)) return true;
        if (Math.abs(prev.steeringWheelDeg - next.steeringWheelDeg) >= cfg.deadbandDeg) return true;
        return nowMs - prev.tsMs >= cfg.heartbeatMs;
    }

    static String normGear(String g) {
        if (g == null) return "D";
        switch (g.trim().toUpperCase()) {
            case "R": case "REVERSE": return "R";
            case "P": case "PARK": return "P";
            case "N": case "NEUTRAL": return "N";
            default: return "D";
        }
    }

    static double clamp(double v, double lo, double hi) {
        if (Double.isNaN(v)) return 0;
        return Math.max(lo, Math.min(hi, v));
    }
}
