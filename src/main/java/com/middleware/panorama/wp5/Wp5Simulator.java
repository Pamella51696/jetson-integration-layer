package com.middleware.panorama.wp5;

/**
 * Fake vehicle for development: steering wheel only (curb zones come from the camera frames, not from here).
 * Enable with -Dwp5.simulate=true. NEVER enable in a real vehicle.
 *
 *   -Dwp5.sim.steerDeg=120   fixed steering angle (default 0)
 *   -Dwp5.sim.sweep=true     sweep +-450 deg in reverse gear instead
 */
final class Wp5Simulator {
    private final Wp5Service svc;
    Wp5Simulator(Wp5Service svc) { this.svc = svc; }

    private static final boolean SWEEP = Boolean.getBoolean("wp5.sim.sweep");
    private static final double FIXED_STEER_DEG = Double.parseDouble(System.getProperty("wp5.sim.steerDeg", "0"));

    void start() {
        Thread t = new Thread(() -> {
            long t0 = System.currentTimeMillis();
            while (!Thread.currentThread().isInterrupted()) {
                double sec = (System.currentTimeMillis() - t0) / 1000.0;
                double steer = SWEEP ? 450.0 * Math.sin(2 * Math.PI * sec / 12.0) : FIXED_STEER_DEG;
                svc.onSteeringInput(steer, "R", 1.5);
                try { Thread.sleep(50); } catch (InterruptedException e) { return; } // 20 Hz
            }
        }, "wp5-simulator");
        t.setDaemon(true);
        t.start();
    }
}
