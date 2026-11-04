package app.weqrom.ai;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

/** Text-only CPU inference. Loading and generation belong on a worker thread. */
public final class OfflineAi {
    public interface Listener { void onPartial(String cumulative); }
    private static final boolean SUPPORTED;
    private static String loadedPath;
    private static long loadedSize, loadedModified;
    static {
        boolean supported;
        try { System.loadLibrary("weqrom_ai"); supported = true; }
        catch (UnsatisfiedLinkError unavailable) { supported = false; }
        SUPPORTED = supported;
    }
    private OfflineAi() { }
    public static boolean isSupported() { return SUPPORTED; }
    public static synchronized boolean load(File path) throws IOException {
        if (!SUPPORTED) throw new IOException("Нужен 64-битный ARM Android 8.0 или новее.");
        File file = path.getCanonicalFile();
        if (!file.isFile() || file.length() == 0) throw new IOException("Встроенная модель отсутствует.");
        if (file.getPath().equals(loadedPath) && file.length() == loadedSize && file.lastModified() == loadedModified) return true;
        loadedPath = null;
        try {
            if (!nativeLoad(file.getPath())) return false;
            loadedPath = file.getPath(); loadedSize = file.length(); loadedModified = file.lastModified();
            return true;
        } catch (RuntimeException failure) {
            nativeRelease();
            throw failure;
        }
    }
    public static long epoch() { return SUPPORTED ? nativeEpoch() : 0; }
    public static void cancel() { if (SUPPORTED) nativeCancel(); }
    public static synchronized String generate(String prompt, int maxTokens, long epoch, Listener listener) {
        if (loadedPath == null) throw new IllegalStateException("Модель ещё не готова.");
        byte[] response = nativeGenerate(prompt.getBytes(StandardCharsets.UTF_8), Math.max(16, Math.min(512, maxTokens)),
            listener == null ? null : new StreamAdapter(listener), epoch);
        return response == null ? "" : new String(response, StandardCharsets.UTF_8);
    }
    public static synchronized void release() {
        if (SUPPORTED) nativeRelease();
        loadedPath = null;
    }
    private static final class StreamAdapter {
        private final Listener listener;
        StreamAdapter(Listener listener) { this.listener = listener; }
        @SuppressWarnings("unused") // JNI calls the complete UTF-8 prefix.
        public void onBytes(byte[] bytes) { listener.onPartial(new String(bytes, StandardCharsets.UTF_8)); }
    }
    private static native boolean nativeLoad(String path);
    private static native byte[] nativeGenerate(byte[] prompt, int maxTokens, StreamAdapter listener, long epoch);
    private static native long nativeEpoch();
    private static native void nativeCancel();
    private static native void nativeRelease();
}
