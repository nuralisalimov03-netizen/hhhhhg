package market.chits;

import java.nio.charset.StandardCharsets;

/** CPU inference backed by llama.cpp. All calls must run off the Android UI thread. */
public final class OfflineAi {
    private static final boolean SUPPORTED;
    private static boolean loaded;
    static {
        boolean ok;
        try {
            System.loadLibrary("chits_ai");
            ok = true;
        } catch (UnsatisfiedLinkError error) {
            ok = false;
        }
        SUPPORTED = ok;
    }
    private OfflineAi() {}
    public static boolean isSupported() { return SUPPORTED; }
    public static synchronized boolean isLoaded() { return loaded; }
    public static synchronized boolean load(String absoluteGgufPath) {
        if (!SUPPORTED) throw new IllegalStateException("Offline AI requires a 64-bit ARM Android device.");
        loaded = false;
        loaded = nativeLoad(absoluteGgufPath);
        return loaded;
    }
    public static synchronized String generate(String userMessage, int maxTokens) {
        if (!SUPPORTED || !loaded) throw new IllegalStateException("Download or import a GGUF model first.");
        if (userMessage == null || userMessage.trim().isEmpty()) throw new IllegalArgumentException("Empty message");
        byte[] bytes = nativeGenerate(userMessage.getBytes(StandardCharsets.UTF_8), Math.max(16, Math.min(256, maxTokens)));
        return bytes == null ? "" : new String(bytes, StandardCharsets.UTF_8).trim();
    }
    public static synchronized void release() {
        if (SUPPORTED) nativeRelease();
        loaded = false;
    }
    private static native boolean nativeLoad(String path);
    private static native byte[] nativeGenerate(byte[] prompt, int maxTokens);
    private static native void nativeRelease();
}
