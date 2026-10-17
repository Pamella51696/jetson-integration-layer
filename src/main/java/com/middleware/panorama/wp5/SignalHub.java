package com.middleware.panorama.wp5;

import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * In-memory publish/subscribe for WP-5 signals.
 *  - remembers the latest message per topic (new clients get current state at once)
 *  - every subscriber has a small bounded queue; when a client is too slow the OLDEST
 *    message is dropped, so a slow phone never blocks the middleware and always
 *    sees the newest steering angle.
 */
public final class SignalHub {

    public static final class Subscriber {
        final BlockingQueue<String> queue = new ArrayBlockingQueue<>(64);
        final java.util.Set<String> topics; // empty = all
        Subscriber(java.util.Set<String> topics) { this.topics = topics; }
        boolean wants(String t) { return topics.isEmpty() || topics.contains(t); }
        public BlockingQueue<String> queue() { return queue; }
    }

    private final Map<String, String> latest = new ConcurrentHashMap<>();
    private final CopyOnWriteArrayList<Subscriber> subs = new CopyOnWriteArrayList<>();

    public Subscriber subscribe(java.util.Set<String> topics) {
        Subscriber s = new Subscriber(topics);
        subs.add(s);
        return s;
    }

    public void unsubscribe(Subscriber s) { subs.remove(s); }

    public int subscriberCount() { return subs.size(); }

    public String latest(String topic) { return latest.get(topic); }

    /** Server-Sent-Events framing: "event: <topic>\ndata: <json>\n\n". */
    public static String frame(String topic, String json) {
        return "event: " + topic + "\ndata: " + json + "\n\n";
    }

    public void publish(String topic, String json) {
        latest.put(topic, json);
        String f = frame(topic, json);
        for (Subscriber s : subs) {
            if (!s.wants(topic)) continue;
            while (!s.queue.offer(f)) s.queue.poll(); // drop oldest
        }
    }
}
