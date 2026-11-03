import market.chits.ModelDownloader;
import java.io.File;
import java.io.IOException;
import java.net.URL;

/** Test-only CLI, including intentional progress-callback cancellation. */
public final class ModelDownloaderHarness {
    public static void main(String[] args) {
        try {
            final long stopAfter = args.length > 5 ? Long.parseLong(args[5]) : Long.MAX_VALUE;
            ModelDownloader.download(new URL(args[0]), new File(args[1]), Long.parseLong(args[2]),
                    args[3], Long.parseLong(args[4]), (current, total) -> {
                        if (current >= stopAfter && current < total) throw new IOException("Test pause");
                    });
            System.out.println("verified");
        } catch (Exception failure) {
            System.err.println(failure.getClass().getSimpleName() + ": " + failure.getMessage());
            System.exit(2);
        }
    }
}
