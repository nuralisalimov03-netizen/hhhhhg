package market.chits;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

/** CPU inference backed by llama.cpp. Run load/generate/release off the UI thread. */
public final class OfflineAi {
    public interface Listener {
        /** Called on the inference worker while tokens are generated. Post UI work yourself. */
        void onPartial(String cumulative);
    }
    private static final boolean SUPPORTED;
    private static boolean loaded;
    private static boolean visionLoaded;
    private static ModelStamp loadedModel;
    private static ModelStamp loadedProjector;
    static {
        boolean ok;
        try { System.loadLibrary("chits_ai"); ok = true; }
        catch (UnsatisfiedLinkError error) { ok = false; }
        SUPPORTED = ok;
    }
    private OfflineAi() {}

    private static final class ModelStamp {
        final String path;
        final long length;
        final long modified;
        ModelStamp(String path) {
            try {
                File file = new File(path).getCanonicalFile();
                if (!file.isFile() || file.length() <= 0)
                    throw new IllegalStateException("Model file is missing or empty.");
                this.path = file.getPath();
                this.length = file.length();
                this.modified = file.lastModified();
            } catch (IOException error) {
                throw new IllegalStateException("Could not inspect model file.", error);
            }
        }
        boolean matches(ModelStamp other) {
            return other != null && path.equals(other.path) && length == other.length && modified == other.modified;
        }
    }
    private static void clearCache() {
        loaded = false;
        visionLoaded = false;
        loadedModel = null;
        loadedProjector = null;
    }
    public static boolean isSupported() { return SUPPORTED; }
    public static synchronized boolean isLoaded() { return loaded; }
    public static synchronized boolean hasVision() { return loaded && visionLoaded; }

    public static synchronized boolean load(String absoluteGgufPath) {
        return load(absoluteGgufPath, null);
    }
    public static synchronized boolean load(String absoluteGgufPath, String absoluteProjectorPath) {
        if (!SUPPORTED) throw new IllegalStateException("Offline AI requires a 64-bit ARM Android device.");
        try {
            ModelStamp model = new ModelStamp(absoluteGgufPath);
            ModelStamp projector = absoluteProjectorPath == null || absoluteProjectorPath.trim().isEmpty()
                    ? null : new ModelStamp(absoluteProjectorPath);
            boolean wantVision = projector != null;
            if (loaded && visionLoaded == wantVision && model.matches(loadedModel)
                    && (!wantVision || projector.matches(loadedProjector))) {
                return true;
            }
            clearCache();
            boolean ok = wantVision ? nativeLoadVision(model.path, projector.path) : nativeLoad(model.path);
            if (ok) {
                loaded = true;
                visionLoaded = wantVision;
                loadedModel = model;
                loadedProjector = projector;
            }
            return ok;
        } catch (RuntimeException error) {
            clearCache();
            nativeRelease();
            throw error;
        }
    }
    public static synchronized String generateStreaming(String userMessage, String imagePath, int maxTokens, Listener listener) {
        long requestEpoch = SUPPORTED ? nativeCancellationEpoch() : 0;
        boolean photo = imagePath != null && !imagePath.trim().isEmpty();
        if (!SUPPORTED || !loaded || (photo && !visionLoaded))
            throw new IllegalStateException(photo ? "Download the vision model and its projector first." : "Download or import a GGUF model first.");
        if (userMessage == null || userMessage.trim().isEmpty()) {
            if (photo) userMessage = "Опиши, что видно на фотографии.";
            else throw new IllegalArgumentException("Empty message");
        }
        byte[] bytes = nativeGenerateStreaming(userMessage.getBytes(StandardCharsets.UTF_8), photo ? imagePath : null,
                Math.max(16, Math.min(256, maxTokens)), listener == null ? null : new StreamAdapter(listener), requestEpoch);
        return bytes == null ? "" : new String(bytes, StandardCharsets.UTF_8).trim();
    }
    public static synchronized String generate(String userMessage, String imagePath, int maxTokens) {
        return generateStreaming(userMessage, imagePath, maxTokens, null);
    }
    public static synchronized String generate(String userMessage, int maxTokens) {
        return generateStreaming(userMessage, null, maxTokens, null);
    }
    /** Does not acquire the generation monitor; safe to call from the Android UI thread. */
    public static void cancel() {
        if (SUPPORTED) nativeCancel();
    }
    public static synchronized void release() {
        if (SUPPORTED) nativeRelease();
        clearCache();
    }
    private static final class StreamAdapter {
        final Listener listener;
        StreamAdapter(Listener listener) { this.listener = listener; }
        @SuppressWarnings("unused") // Called from JNI on every throttled, complete UTF-8 prefix.
        public void onBytes(byte[] cumulative) {
            listener.onPartial(new String(cumulative, StandardCharsets.UTF_8));
        }
    }
    private static native boolean nativeLoad(String path);
    private static native boolean nativeLoadVision(String path, String projectorPath);
    private static native byte[] nativeGenerateStreaming(byte[] prompt, String imagePath, int maxTokens, StreamAdapter adapter, long requestEpoch);
    private static native long nativeCancellationEpoch();
    private static native void nativeCancel();
    private static native void nativeRelease();
}
