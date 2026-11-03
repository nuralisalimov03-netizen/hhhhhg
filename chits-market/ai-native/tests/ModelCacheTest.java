package market.chits;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Tests actual OfflineAi Java lifecycle with a counting JNI fixture, not model inference. */
public final class ModelCacheTest {
    private static native int loads();
    private static native int cancels();
    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
    public static void main(String[] args) throws Exception {
        check(OfflineAi.isSupported(), "Test JNI library did not load");
        Path directory = Files.createTempDirectory("chits-model-cache-");
        try {
            Path model = Files.write(directory.resolve("model.gguf"), new byte[]{1,2,3});
            Path projector = Files.write(directory.resolve("mmproj.gguf"), new byte[]{4,5,6});
            Path reject = Files.write(directory.resolve("reject.gguf"), new byte[]{7});
            check(OfflineAi.load(model.toString()) && loads() == 1, "Initial model did not load once");
            check(OfflineAi.load(directory.resolve(".").resolve("model.gguf").toString()) && loads() == 1,
                    "Same canonical model reloaded on repeated request");
            check(OfflineAi.load(model.toString(), projector.toString()) && loads() == 2 && OfflineAi.hasVision(), "Vision mode did not switch models");
            check(OfflineAi.load(model.toString(), projector.toString()) && loads() == 2, "Unchanged projector reloaded");
            check(projector.toFile().setLastModified(projector.toFile().lastModified() + 3000), "Could not update fixture timestamp");
            check(OfflineAi.load(model.toString(), projector.toString()) && loads() == 3, "Changed projector timestamp did not invalidate cache");
            Files.write(model, new byte[]{1,2,3,4});
            check(OfflineAi.load(model.toString(), projector.toString()) && loads() == 4, "Changed model length did not invalidate cache");
            check(!OfflineAi.load(reject.toString()) && !OfflineAi.isLoaded() && loads() == 5, "Failed load left stale loaded state");
            check(OfflineAi.load(model.toString()) && loads() == 6 && !OfflineAi.hasVision(), "Text model was incorrectly reused after failed load");
            OfflineAi.release();
            check(!OfflineAi.isLoaded(), "Release retained loaded state");
            check(OfflineAi.load(model.toString()) && loads() == 7, "Release did not invalidate model cache");
            try { OfflineAi.load(directory.resolve("missing.gguf").toString()); throw new AssertionError("Missing model accepted"); }
            catch (IllegalStateException expected) { check(!OfflineAi.isLoaded(), "Missing model left stale cache"); }
            CountDownLatch monitorHeld = new CountDownLatch(1);
            CountDownLatch releaseMonitor = new CountDownLatch(1);
            Thread generationMonitor = new Thread(new Runnable() {
                public void run() {
                    synchronized (OfflineAi.class) {
                        monitorHeld.countDown();
                        try { releaseMonitor.await(2, TimeUnit.SECONDS); }
                        catch (InterruptedException error) { Thread.currentThread().interrupt(); }
                    }
                }
            });
            generationMonitor.start();
            check(monitorHeld.await(1, TimeUnit.SECONDS), "Generation monitor fixture failed");
            long before = System.nanoTime();
            OfflineAi.cancel();
            long elapsed = System.nanoTime() - before;
            releaseMonitor.countDown();
            generationMonitor.join();
            check(elapsed < TimeUnit.MILLISECONDS.toNanos(500) && cancels() == 1, "Cancel blocked behind generation monitor");
            System.out.println("PASS: canonical model reuse, mode/file invalidation, failed-load/release invalidation, lock-free cancel entry");
        } finally {
            OfflineAi.release();
            for (Path file : Files.newDirectoryStream(directory)) Files.deleteIfExists(file);
            Files.deleteIfExists(directory);
        }
    }
}
