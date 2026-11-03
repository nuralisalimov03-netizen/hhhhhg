import market.chits.LocalApiTransport;
import org.json.JSONObject;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;

/** Host tests only; request files contain disposable test credentials. */
public final class LocalApiTransportHarness {
    public static void main(String[] args) {
        try {
            JSONObject request = new JSONObject(new String(Files.readAllBytes(Paths.get(args[1])), StandardCharsets.UTF_8));
            File upload = args.length > 2 && !args[2].equals("-") ? new File(args[2]) : null;
            String name = args.length > 3 ? args[3] : "fixture.apk";
            System.out.println(LocalApiTransport.execute(args[0], request, upload, name).toString());
        } catch (Exception error) {
            System.err.println("REJECTED " + error.getMessage());
            System.exit(2);
        }
    }
}
