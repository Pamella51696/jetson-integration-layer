package com.middleware.panorama.wp5;

/**
 * All tunable WP-5 numbers in one place (vehicle geometry, zone thresholds).
 * Zones come ONLY from the camera frames (CurbVision) - there are no distance sensors any more.
 * Override any value with -Dwp5.<name>=... (e.g. -Dwp5.wheelbaseM=2.85).
 *
 * IMPORTANT: the defaults are generic mid-size-car values. Replace them with
 * the real numbers of your test vehicle (data sheet / OEM) before demos.
 */
public final class Wp5Config {

    // ---- Vehicle geometry (metres) ----
    public final double wheelbaseM;          // front axle <-> rear axle
    public final double trackWidthM;         // left wheel <-> right wheel
    public final double vehicleWidthM;       // body width (for guide lines)
    // ---- Steering ----
    public final double steeringRatio;       // steering wheel deg per 1 deg road-wheel
    public final double maxSteeringWheelDeg; // lock-to-lock / 2 (clamp)
    public final double deadbandDeg;         // do not publish changes smaller than this
    public final long   heartbeatMs;         // re-publish unchanged state at least this often
    public final double pathLengthM;         // predicted path length
    public final int    pathPoints;          // points per guide line
    // ---- Curb zones, measured from the camera frames (metres from the car body to the curb) ----
    public final double dangerM;             // d <= dangerM  -> RED
    public final double warningM;            // d <= warningM -> YELLOW, else GREEN
    public final double maxRangeM;           // beyond this -> GREEN (nothing close)
    public final double hysteresisM;         // anti-flicker band
    public final long   visionStaleMs;       // a side nobody refreshes for this long disappears (detector stopped)
    // ---- Security ----
    public final String ingestToken;        // "" = no auth (lab only!)
    // ---- Customisation file (3D node names, axes, colours, panorama map) ----
    public final VehicleProfile profile;
    public final String profileSource;

    private Wp5Config() {
        VehicleProfile p; String src;
        String path = System.getProperty("wp5.profile", "");
        if (path.isBlank()) {   // working dir first, then up to 4 parents (e.g. started from src/main/java)
            java.nio.file.Path d = java.nio.file.Paths.get("").toAbsolutePath();
            for (int i = 0; i < 5 && d != null && path.isBlank(); i++, d = d.getParent())
                if (java.nio.file.Files.exists(d.resolve("vehicle-profile.json")))
                    path = d.resolve("vehicle-profile.json").toString();
        }
        if (path.isBlank() || path.equals("none")) { p = VehicleProfile.defaults(); src = "built-in defaults (placeholder node names!)"; }
        else {
            try { p = VehicleProfile.load(java.nio.file.Paths.get(path)); src = path; }
            catch (Exception e) {
                System.err.println("[WP5] !!! CANNOT LOAD PROFILE '" + path + "': " + e.getMessage());
                System.err.println("[WP5] !!! falling back to built-in defaults — node names will NOT match your model");
                p = VehicleProfile.defaults(); src = "built-in defaults (profile failed to load)";
            }
        }
        profile = p; profileSource = src;
        wheelbaseM          = d("wheelbaseM", 2.70);
        trackWidthM         = d("trackWidthM", 1.60);
        vehicleWidthM       = d("vehicleWidthM", 1.85);
        steeringRatio       = d("steeringRatio", 15.5);
        maxSteeringWheelDeg = d("maxSteeringWheelDeg", 540.0);
        deadbandDeg         = d("deadbandDeg", 0.5);
        heartbeatMs         = (long) d("heartbeatMs", 1000);
        pathLengthM         = d("pathLengthM", 5.0);
        pathPoints          = (int) d("pathPoints", 12);
        dangerM             = d("dangerM", 0.50);
        warningM            = d("warningM", 1.00);
        maxRangeM           = d("maxRangeM", 2.50);
        hysteresisM         = d("hysteresisM", 0.05);
        visionStaleMs       = (long) d("visionStaleMs", 1000);
        ingestToken         = System.getProperty("wp5.ingestToken", "");
    }

    public static Wp5Config load() { return new Wp5Config(); }

    /** Colour for a zone: profile "colors" section, else the built-in colour. */
    public String color(Zone z) {
        String c = profile.color(z.name());
        return c != null ? c : z.color;
    }

    /** Priority: -Dwp5.key  >  profile file  >  built-in default. */
    private double d(String key, double def) {
        String v = System.getProperty("wp5." + key);
        if (v == null || v.isBlank()) {
            Double pv = profile.number(key);
            return pv != null ? pv : def;
        }
        try { return Double.parseDouble(v.trim()); }
        catch (NumberFormatException e) {
            System.err.println("[WP5] bad value for wp5." + key + "='" + v + "', using " + def);
            return def;
        }
    }
}
