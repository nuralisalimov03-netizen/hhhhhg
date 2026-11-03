package market.chits;

import java.nio.charset.StandardCharsets;

/** CPU inference backed by llama.cpp. All calls must run off the Android UI thread. */
public final class OfflineAi {
    private static final boolean SUPPORTED;
    private static boolean loaded;
    private static boolean visionLoaded;
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
    public static synchronized boolean hasVision() { return loaded && visionLoaded; }
    public static synchronized boolean load(String absoluteGgufPath) {
        if (!SUPPORTED) throw new IllegalStateException("Offline AI requires a 64-bit ARM Android device.");
        loaded = false;
        visionLoaded = false;
        loaded = nativeLoad(absoluteGgufPath);
        return loaded;
    }
    public static synchronized boolean load(String absoluteGgufPath, String absoluteProjectorPath) {
        if (absoluteProjectorPath == null || absoluteProjectorPath.trim().isEmpty()) return load(absoluteGgufPath);
        if (!SUPPORTED) throw new IllegalStateException("Offline AI requires a 64-bit ARM Android device.");
        loaded = false;
        visionLoaded = false;
        loaded = nativeLoadVision(absoluteGgufPath, absoluteProjectorPath);
        visionLoaded = loaded;
        return loaded;
    }
    public static synchronized String generate(String userMessage, String imagePath, int maxTokens) {
        if (imagePath == null || imagePath.trim().isEmpty()) return generate(userMessage, maxTokens);
        if (!SUPPORTED || !loaded || !visionLoaded)
            throw new IllegalStateException("Download the vision model and its projector first.");
        if (userMessage == null || userMessage.trim().isEmpty()) userMessage = "Опиши, что видно на фотографии.";
        byte[] bytes = nativeGenerateVision(userMessage.getBytes(StandardCharsets.UTF_8), imagePath, Math.max(16, Math.min(256, maxTokens)));
        return bytes == null ? "" : new String(bytes, StandardCharsets.UTF_8).trim();
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
        visionLoaded = false;
    }
    private static native boolean nativeLoad(String path);
    private static native boolean nativeLoadVision(String path, String projectorPath);
    private static native byte[] nativeGenerateVision(byte[] prompt, String imagePath, int maxTokens);
    private static native byte[] nativeGenerate(byte[] prompt, int maxTokens);
    private static native void nativeRelease();
}
