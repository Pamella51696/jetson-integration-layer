package com.middleware.panorama.wp5;

/**
 * WP-5 / Package 1 — one immutable "steering + wheel" snapshot.
 *
 * Conventions (ISO 8855): angles in degrees, POSITIVE = LEFT (counter-clockwise
 * seen from above). Vehicle frame: origin = rear-axle centre, +x forward, +y left, metres.
 */
public final class SteeringState {
    public final long seq;
    public final long tsMs;
    public final double steeringWheelDeg;   // what the driver's wheel shows
    public final double roadWheelAvgDeg;    // bicycle-model average road-wheel angle
    public final double leftWheelDeg;       // front-left steer angle (Ackermann)
    public final double rightWheelDeg;      // front-right steer angle (Ackermann)
    public final double curvature;          // 1/m, + = left, 0 = straight
    public final double turnRadiusM;        // 0 = straight (infinite)
    public final String direction;          // LEFT | RIGHT | STRAIGHT
    public final String gear;               // P | R | N | D
    public final double speedKph;
    public final double[][] guideLeft;      // [n][2] x,y points of left body edge path
    public final double[][] guideRight;     // [n][2] x,y points of right body edge path
    public final String modelJson;          // ready-to-apply 3D transforms (see SteeringModel)
    public final boolean valid;             // false = vehicle-bus data is stale, do not trust

    SteeringState(long seq, long tsMs, double steeringWheelDeg, double roadWheelAvgDeg,
                  double leftWheelDeg, double rightWheelDeg, double curvature,
                  double turnRadiusM, String direction, String gear, double speedKph,
                  double[][] guideLeft, double[][] guideRight, String modelJson, boolean valid) {
        this.seq = seq; this.tsMs = tsMs;
        this.steeringWheelDeg = steeringWheelDeg; this.roadWheelAvgDeg = roadWheelAvgDeg;
        this.leftWheelDeg = leftWheelDeg; this.rightWheelDeg = rightWheelDeg;
        this.curvature = curvature; this.turnRadiusM = turnRadiusM;
        this.direction = direction; this.gear = gear; this.speedKph = speedKph;
        this.guideLeft = guideLeft; this.guideRight = guideRight;
        this.modelJson = modelJson;
        this.valid = valid;
    }

    /** Copy with a new seq/timestamp/validity (used for heartbeats and stale marking). */
    public SteeringState reissue(long newSeq, long newTsMs, boolean newValid) {
        return new SteeringState(newSeq, newTsMs, steeringWheelDeg, roadWheelAvgDeg, leftWheelDeg,
                rightWheelDeg, curvature, turnRadiusM, direction, gear, speedKph, guideLeft, guideRight, modelJson, newValid);
    }

    public String toJson() {
        StringBuilder sb = new StringBuilder(512);
        sb.append("{\"type\":\"steering\",\"v\":1")
          .append(",\"seq\":").append(seq)
          .append(",\"ts\":").append(tsMs)
          .append(",\"valid\":").append(valid)
          .append(",\"steeringWheelDeg\":").append(Json.num(steeringWheelDeg))
          .append(",\"roadWheelAvgDeg\":").append(Json.num(roadWheelAvgDeg))
          .append(",\"leftWheelDeg\":").append(Json.num(leftWheelDeg))
          .append(",\"rightWheelDeg\":").append(Json.num(rightWheelDeg))
          .append(",\"curvature\":").append(Json.num(curvature))
          .append(",\"turnRadiusM\":").append(Json.num(turnRadiusM))
          .append(",\"direction\":").append(Json.str(direction))
          .append(",\"gear\":").append(Json.str(gear))
          .append(",\"speedKph\":").append(Json.num(speedKph))
          .append(",\"frame\":\"rear_axle_x_fwd_y_left_m\"")
          .append(",\"guideLeft\":").append(pts(guideLeft))
          .append(",\"guideRight\":").append(pts(guideRight))
          .append(",\"model\":").append(modelJson)
          .append('}');
        return sb.toString();
    }

    private static String pts(double[][] p) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < p.length; i++) {
            if (i > 0) sb.append(',');
            sb.append('[').append(Json.num(p[i][0])).append(',').append(Json.num(p[i][1])).append(']');
        }
        return sb.append(']').toString();
    }
}
