package com.middleware.panorama.wp5;

import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Tiny dependency-free JSON helpers (the project has no JSON library). */
final class Json {
    private Json() {}

    /** Fixed 3-decimals, '.' separator regardless of server locale, no NaN/Inf. */
    static String num(double v) {
        if (Double.isNaN(v) || Double.isInfinite(v)) return "0";
        String s = String.format(Locale.ROOT, "%.3f", v);
        if (s.equals("-0.000")) s = "0.000";
        return s;
    }

    static String str(String s) {
        if (s == null) return "null";
        StringBuilder sb = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c)); else sb.append(c);
            }
        }
        return sb.append('"').toString();
    }

    /** Reads a numeric field from a flat JSON object. Returns def when missing. */
    static double getNum(String json, String key, double def) {
        Matcher m = Pattern.compile("\"" + Pattern.quote(key)
                + "\"\\s*:\\s*(-?\\d+(?:\\.\\d+)?(?:[eE][-+]?\\d+)?)").matcher(json);
        return m.find() ? Double.parseDouble(m.group(1)) : def;
    }

    /** Reads a boolean field (true/false, unquoted) from a flat JSON object. */
    static boolean getBool(String json, String key, boolean def) {
        Matcher m = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*(true|false)").matcher(json);
        return m.find() ? Boolean.parseBoolean(m.group(1)) : def;
    }

    /** Reads a string field from a flat JSON object. Returns def when missing. */
    static String getStr(String json, String key, String def) {
        Matcher m = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\"([^\"]*)\"").matcher(json);
        return m.find() ? m.group(1) : def;
    }
}
