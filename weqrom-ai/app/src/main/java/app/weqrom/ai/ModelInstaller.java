package app.weqrom.ai;

import android.content.Context;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Locale;

/** No downloads: the signed APK is the sole source of model bytes. */
final class ModelInstaller {
    static final String ASSET = "model/weqrom-seed-f16.gguf";
    static JSONObject metadata(Context context) throws Exception {
        byte[] content;
        try (InputStream input = context.getAssets().open("model/manifest.json"); ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096]; int count;
            while ((count = input.read(buffer)) != -1) {
                if (bytes.size() + count > 65536) throw new IOException("Слишком большой манифест модели.");
                bytes.write(buffer, 0, count);
            }
            content = bytes.toByteArray();
        }
        JSONObject data = new JSONObject(new String(content, StandardCharsets.UTF_8));
        if (!data.getBoolean("trained_from_scratch") || data.getLong("parameters") <= 0 || data.getLong("bytes") < 8
                || data.getLong("bytes") > 1024L * 1024 * 1024 || !data.getString("sha256").matches("[0-9a-f]{64}")
                || data.getString("name").isEmpty() || data.getString("name").length() > 120)
            throw new IOException("Некорректный манифест встроенной модели.");
        return data;
    }
    static synchronized File restore(Context context, JSONObject metadata) throws Exception {
        File target = new File(context.getFilesDir(), "weqrom-seed-f16.gguf");
        long expected = metadata.getLong("bytes"); String expectedSha = metadata.getString("sha256");
        if (target.isFile() && target.length() == expected && digest(target).equals(expectedSha)) return target;
        if (context.getFilesDir().getUsableSpace() < expected + 64L * 1024 * 1024)
            throw new IOException("Недостаточно места для встроенной модели. Освободите " + (expected / (1024 * 1024) + 64) + " МиБ.");
        File partial = new File(context.getFilesDir(), "weqrom-seed-f16.gguf.part");
        boolean complete = false;
        try {
            MessageDigest hash = MessageDigest.getInstance("SHA-256"); long total = 0;
            try (InputStream input = context.getAssets().open(ASSET); FileOutputStream output = new FileOutputStream(partial)) {
                byte[] buffer = new byte[65536]; int count;
                while ((count = input.read(buffer)) != -1) {
                    total += count;
                    if (total > expected) throw new IOException("Размер встроенной модели не совпал.");
                    hash.update(buffer, 0, count); output.write(buffer, 0, count);
                }
                output.getFD().sync();
            }
            if (total != expected || !hex(hash.digest()).equals(expectedSha)) throw new IOException("Нарушена целостность встроенной модели.");
            if (!partial.renameTo(target)) throw new IOException("Не удалось сохранить встроенную модель.");
            complete = true;
            return target;
        } finally { if (!complete) partial.delete(); }
    }
    private static String digest(File file) throws Exception {
        MessageDigest hash = MessageDigest.getInstance("SHA-256");
        try (InputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[65536]; int count;
            while ((count = input.read(buffer)) != -1) hash.update(buffer, 0, count);
        }
        return hex(hash.digest());
    }
    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder();
        for (byte value : bytes) result.append(String.format(Locale.ROOT, "%02x", value & 255));
        return result.toString();
    }
}
