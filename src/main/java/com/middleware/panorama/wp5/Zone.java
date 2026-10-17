package com.middleware.panorama.wp5;

/** Safety zone shown on the panorama. Android maps level/color to its overlay. */
public enum Zone {
    NONE(0, "#9E9E9E"),     // no curb in that camera frame (summary only)
    SAFE(1, "#34C759"),     // green
    WARNING(2, "#FFCC00"),  // yellow
    DANGER(3, "#FF3B30");   // red

    public final int level;
    public final String color;
    Zone(int level, String color) { this.level = level; this.color = color; }

    /** Worse = more dangerous. NONE never wins over a real reading. */
    public static Zone worst(Zone a, Zone b) {
        if (a == NONE) return b;
        if (b == NONE) return a;
        return a.level >= b.level ? a : b;
    }
}
