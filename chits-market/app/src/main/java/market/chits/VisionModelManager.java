package market.chits;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.SystemClock;
import android.util.AtomicFile;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Owns the two-file Qwen vision profile. The manifest pins both components to
 * one official immutable revision. A committed ready flag is the publication
 * boundary: half-installed pairs are never reported as usable.
 *
 * start(false) waits for unmetered Wi-Fi/Ethernet. start(true) is the explicit
 * user action permitting mobile data. Partial files survive transient failures.
 */
public final class VisionModelManager {
    public interface Listener { void onEvent(JSONObject event); }

    private static final String REPO = "ggml-org/Qwen2.5-VL-3B-Instruct-GGUF";
    private static final String MODEL_LABEL = "Qwen2.5-VL 3B Instruct · Q4_K_M";
    private static final long COMPONENT_LIMIT = 4L * 1024 * 1024 * 1024;
    private static final long PROFILE_LIMIT = 6L * 1024 * 1024 * 1024;
    private static final long SPACE_MARGIN = 256L * 1024 * 1024;
    private static final int JSON_LIMIT = 4 * 1024 * 1024;

    private final Context context;
    private final SharedPreferences prefs;
    private volatile Listener listener;
    private final File directory;
    private final AtomicFile manifestFile;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final AtomicBoolean busy = new AtomicBoolean(false);
    private volatile String status = "waiting-wifi";
    private volatile String error = "";
    private volatile String component = "";
    private volatile long downloadedBytes;
    private volatile long totalBytes;
    private volatile int progress;
    private volatile long lastAutoAttempt;
    private volatile long lastProgressEvent;
    private volatile int lastProgressPercent = -1;
    private final android.os.Handler retryHandler = new android.os.Handler(android.os.Looper.getMainLooper());
    private final AtomicBoolean retryScheduled = new AtomicBoolean(false);
    private final Runnable autoRetry = () -> { retryScheduled.set(false); start(false); };
    private volatile long lastNetworkCheck;

    public VisionModelManager(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
        prefs = this.context.getSharedPreferences("vision-model", Context.MODE_PRIVATE);
        directory = new File(this.context.getFilesDir(), "vision-downloads");
        directory.mkdirs();
        manifestFile = new AtomicFile(new File(directory, "manifest.json"));
        if (isVisionReady()) {
            status = "ready";
            totalBytes = prefs.getLong("modelBytes", 0) + prefs.getLong("projectorBytes", 0);
            downloadedBytes = totalBytes;
            progress = 100;
        }
    }

    /** These are the committed vision paths; the legacy text model is separate. */
    public File modelFile() { return new File(context.getFilesDir(), "assistant-vision.gguf"); }
    public File projectorFile() { return new File(context.getFilesDir(), "assistant-mmproj.gguf"); }
    public File legacyFile() { return new File(context.getFilesDir(), "assistant.gguf"); }
    public boolean isBusy() { return busy.get(); }
    /** Reattach the current Activity to a process-retained manager; null detaches. */
    public synchronized void setListener(Listener listener) { this.listener = listener; }
    public synchronized void clearListener(Listener expected) {
        if (listener == expected) listener = null;
    }

    public boolean isVisionReady() {
        if (!prefs.getBoolean("ready", false)) return false;
        String revision = prefs.getString("readyRevision", "");
        return revision.matches("[0-9a-f]{40}")
            && modelFile().isFile() && projectorFile().isFile()
            && modelFile().length() == prefs.getLong("modelBytes", -1)
            && projectorFile().length() == prefs.getLong("projectorBytes", -1);
    }

    public JSONObject state() {
        boolean supported = OfflineAi.isSupported();
        boolean vision = supported && isVisionReady();
        boolean legacy = supported && legacyFile().isFile();
        String reason;
        if (!supported) reason = "Для локального ИИ нужен 64-битный Android ARM64";
        else if (!error.isEmpty()) reason = error;
        else if (vision) reason = "Модель и зрение готовы: фото обрабатываются на телефоне без интернета";
        else if (busy.get()) reason = "Загружаются языковая модель и модуль зрения. После обрыва загрузку можно продолжить";
        else reason = "Qwen со зрением загрузится автоматически по Wi-Fi. Для загрузки через мобильную сеть нажмите кнопку";
        return object("available", vision || legacy, "installed", vision || legacy,
            "vision", vision, "downloading", busy.get(), "status", status,
            "progress", progress, "downloadedBytes", downloadedBytes, "totalBytes", totalBytes,
            "component", component, "model", vision || !legacy ? MODEL_LABEL : "Qwen2.5 0.5B · текст; зрение готовится",
            "targetModel", MODEL_LABEL, "autoDownload", true, "reason", reason);
    }

    /** Nonblocking and idempotent. A manual invocation also bypasses retry delay. */
    public void start(boolean allowMetered) {
        if (isVisionReady()) { status = "ready"; return; }
        if (!OfflineAi.isSupported()) {
            status = "error";
            error = "Для ИИ с фотографиями нужен Android ARM64";
            emit();
            return;
        }
        if (busy.get()) return;
        if (!networkAllowed(allowMetered)) {
            status = "waiting-wifi";
            error = "";
            emit();
            return;
        }
        long now = SystemClock.elapsedRealtime();
        if (!allowMetered && lastAutoAttempt != 0 && now - lastAutoAttempt < 60000) {
            scheduleAutoRetry(60000 - (now - lastAutoAttempt));
            return;
        }
        if (!busy.compareAndSet(false, true)) return;
        lastAutoAttempt = now;
        status = "downloading";
        error = "";
        component = "Проверка версии модели";
        emit();
        worker.execute(() -> prepare(allowMetered));
    }

    private boolean networkAllowed(boolean allowMetered) {
        try {
            ConnectivityManager cm = (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
            if (cm == null) return false;
            Network network = cm.getActiveNetwork();
            NetworkCapabilities capabilities = network == null ? null : cm.getNetworkCapabilities(network);
            if (capabilities == null || !capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                    || !capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)) return false;
            if (allowMetered) return true;
            return !cm.isActiveNetworkMetered()
                && (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
                    || capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET));
        } catch (SecurityException e) { return false; }
    }

    private void prepare(boolean allowMetered) {
        try {
            JSONObject manifest = manifest();
            JSONObject language = manifest.getJSONObject("language");
            JSONObject projector = manifest.getJSONObject("projector");
            long languageSize = language.getLong("size");
            long projectorSize = projector.getLong("size");
            totalBytes = languageSize + projectorSize;
            File languagePart = candidate(modelFile(), new File(directory, "language.part"), languageSize);
            File projectorPart = candidate(projectorFile(), new File(directory, "projector.part"), projectorSize);
            long present = boundedLength(languagePart, languageSize) + boundedLength(projectorPart, projectorSize);
            downloadedBytes = present;
            progress = (int) (present * 100 / totalBytes);
            long remaining = totalBytes - present;
            if (context.getFilesDir().getUsableSpace() < remaining + SPACE_MARGIN)
                throw new IOException("Недостаточно места: для модели со зрением нужно ещё " + megabytes(remaining + SPACE_MARGIN) + " МБ");
            transfer(manifest, language, languagePart, projectorPart, projectorSize, "Языковая модель Qwen", allowMetered);
            transfer(manifest, projector, projectorPart, languagePart, languageSize, "Модуль зрения", allowMetered);

            component = "Проверка модели и зрения";
            downloadedBytes = totalBytes;
            progress = 100;
            emit();
            // Validation runs only on this worker, never on Android's UI thread.
            try {
                if (!OfflineAi.load(languagePart.getAbsolutePath(), projectorPart.getAbsolutePath()))
                    throw new IOException("Не удалось загрузить модель со зрением: проверьте свободную память");
            } finally { OfflineAi.release(); }
            if (!prefs.edit().putBoolean("ready", false).commit()) throw new IOException("Не удалось сохранить состояние модели");
            move(languagePart, modelFile());
            move(projectorPart, projectorFile());
            if (!prefs.edit().putLong("modelBytes", languageSize).putLong("projectorBytes", projectorSize)
                    .putString("modelSha256", language.getString("sha256"))
                    .putString("projectorSha256", projector.getString("sha256"))
                    .putString("readyRevision", manifest.getString("revision")).putBoolean("ready", true).commit())
                throw new IOException("Не удалось завершить установку модели");
            status = "ready";
            component = "";
            error = "";
        } catch (WaitingForWifi e) {
            status = "waiting-wifi";
            error = "";
            lastAutoAttempt = 0;
            retryHandler.removeCallbacks(autoRetry);
            retryScheduled.set(false);
        } catch (Exception e) {
            if (!allowMetered && !networkAllowed(false)) {
                status = "waiting-wifi";
                error = "";
            } else {
                status = "error";
                String detail = e.getMessage();
                error = "Загрузка Qwen не завершена. Сохранённую часть можно продолжить. "
                    + (detail == null ? "Проверьте интернет и свободное место" : detail);
            }
        } finally {
            busy.set(false);
            emit();
            // A validated Wi-Fi callback can arrive just before busy clears.
            // Do not lose that sole reconnect event or leave the UI waiting.
            if ("waiting-wifi".equals(status) && networkAllowed(false)) scheduleAutoRetry(300);
        }
    }

    private void scheduleAutoRetry(long delayMillis) {
        if (retryScheduled.compareAndSet(false, true)) retryHandler.postDelayed(autoRetry, Math.max(250, delayMillis));
    }

    private void transfer(JSONObject manifest, JSONObject entry, File target, File other,
                          long otherSize, String label, boolean allowMetered) throws Exception {
        component = label;
        if (!networkAllowed(allowMetered) && target.length() < entry.getLong("size")) throw new WaitingForWifi();
        URL url = new URI("https", "huggingface.co", "/" + REPO + "/resolve/"
            + manifest.getString("revision") + "/" + entry.getString("path"), null).toURL();
        long expected = entry.getLong("size");
        ModelDownloader.download(url, target, expected, entry.getString("sha256"), COMPONENT_LIMIT, (current, total) -> {
            long now = SystemClock.elapsedRealtime();
            if (current < total && now - lastNetworkCheck > 1500) {
                lastNetworkCheck = now;
                if (!networkAllowed(allowMetered)) throw new WaitingForWifi();
            }
            downloadedBytes = current + boundedLength(other, otherSize);
            progress = (int) Math.min(100, downloadedBytes * 100 / totalBytes);
            if (progress != lastProgressPercent || now - lastProgressEvent > 350) {
                lastProgressPercent = progress;
                lastProgressEvent = now;
                emit();
            }
        });
    }

    private JSONObject manifest() throws Exception {
        if (manifestFile.getBaseFile().exists()) {
            try {
                if (manifestFile.getBaseFile().length() > JSON_LIMIT) throw new IOException("Слишком большой манифест");
                byte[] data = manifestFile.readFully();
                if (data.length > JSON_LIMIT) throw new IOException("Слишком большой манифест");
                JSONObject existing = new JSONObject(new String(data, "UTF-8"));
                validateManifest(existing);
                return existing;
            } catch (Exception invalid) {
                // A corrupt local manifest must never mix a new revision with old parts.
                manifestFile.delete();
                new File(directory, "language.part").delete();
                new File(directory, "projector.part").delete();
            }
        }
        String api = "https://huggingface.co/api/models/" + REPO;
        String revision = new JSONObject(readText(api + "/revision/main")).getString("sha");
        if (!revision.matches("[0-9a-f]{40}")) throw new IOException("Не удалось закрепить версию официальной модели");
        JSONArray tree = new JSONArray(readText(api + "/tree/" + revision + "?recursive=false&limit=1000"));
        JSONObject result = selectManifest(revision, tree);
        FileOutputStream output = null;
        try {
            output = manifestFile.startWrite();
            output.write(result.toString().getBytes("UTF-8"));
            manifestFile.finishWrite(output);
        } catch (Exception e) {
            if (output != null) manifestFile.failWrite(output);
            throw e;
        }
        return result;
    }

    /** Pure metadata selection, exercised on the host without weights or Android. */
    private static JSONObject selectManifest(String revision, JSONArray tree) throws Exception {
        if (!revision.matches("[0-9a-f]{40}")) throw new IOException("Некорректная ревизия модели");
        List<JSONObject> languages = new ArrayList<>();
        List<JSONObject> projectors = new ArrayList<>();
        for (int i = 0; i < tree.length(); i++) {
            JSONObject record = tree.optJSONObject(i);
            if (record == null) continue;
            String path = record.optString("path");
            String name = path.toLowerCase(Locale.ROOT);
            if (!name.endsWith(".gguf") || name.contains("/") || name.contains("..")) continue;
            if (name.startsWith("mmproj")) projectors.add(record);
            else if (name.contains("q4_k_m")) languages.add(record);
        }
        if (languages.size() != 1 || projectors.isEmpty())
            throw new IOException("Не найдена однозначная пара Q4_K_M и mmproj в официальном репозитории");
        Collections.sort(projectors, new Comparator<JSONObject>() {
            public int compare(JSONObject a, JSONObject b) {
                int rank = projectorRank(a.optString("path")) - projectorRank(b.optString("path"));
                return rank != 0 ? rank : a.optString("path").compareTo(b.optString("path"));
            }
        });
        JSONObject selectedProjector = projectors.get(0);
        if (projectorRank(selectedProjector.optString("path")) >= 2)
            throw new IOException("Официальный модуль зрения Q8_0/F16 не найден");
        JSONObject result = object("version", 1, "repo", REPO, "revision", revision,
            "language", entry(languages.get(0)), "projector", entry(selectedProjector));
        validateManifest(result);
        return result;
    }

    private static int projectorRank(String path) {
        String name = path.toLowerCase(Locale.ROOT);
        if (name.contains("q8_0")) return 0;
        if (name.contains("f16")) return 1;
        return 2;
    }

    private static JSONObject entry(JSONObject record) throws Exception {
        JSONObject lfs = record.getJSONObject("lfs");
        return object("path", record.getString("path"), "size", record.getLong("size"), "sha256", lfs.getString("oid"));
    }

    private static void validateManifest(JSONObject manifest) throws Exception {
        if (manifest.getInt("version") != 1 || !REPO.equals(manifest.getString("repo"))
                || !manifest.getString("revision").matches("[0-9a-f]{40}")) throw new IOException("Неверный манифест модели");
        long total = 0;
        for (String key : new String[]{"language", "projector"}) {
            JSONObject entry = manifest.getJSONObject(key);
            String name = entry.getString("path");
            long size = entry.getLong("size");
            if (name.contains("/") || name.contains("\\") || name.contains("..")
                    || !name.toLowerCase(Locale.ROOT).endsWith(".gguf") || size <= 0 || size > COMPONENT_LIMIT
                    || !entry.getString("sha256").matches("[0-9a-fA-F]{64}")) throw new IOException("Некорректный файл в манифесте модели");
            if (key.equals("language") && (!name.toLowerCase(Locale.ROOT).contains("q4_k_m") || name.toLowerCase(Locale.ROOT).startsWith("mmproj")))
                throw new IOException("В манифесте не языковая модель Q4_K_M");
            if (key.equals("projector") && (!name.toLowerCase(Locale.ROOT).startsWith("mmproj") || projectorRank(name) >= 2))
                throw new IOException("В манифесте не модуль зрения Q8_0/F16");
            total += size;
        }
        if (total > PROFILE_LIMIT) throw new IOException("Выбранная модель слишком велика для этой версии приложения");
    }

    private static File candidate(File committed, File partial, long expected) {
        // Recover safely if a process stopped between the two final renames.
        return committed.isFile() && committed.length() == expected ? committed : partial;
    }
    private static long boundedLength(File file, long expected) {
        return file.isFile() ? Math.min(expected, Math.max(0, file.length())) : 0;
    }
    private static void move(File from, File to) throws IOException {
        if (from.equals(to)) return;
        if (!from.renameTo(to)) throw new IOException("Не удалось сохранить проверенный файл модели");
    }
    private static long megabytes(long bytes) { return (bytes + 1024 * 1024 - 1) / (1024 * 1024); }

    private String readText(String address) throws Exception {
        URL url = new URL(address);
        for (int redirects = 0; redirects < 8; redirects++) {
            if (!"https".equals(url.getProtocol()) || url.getUserInfo() != null) throw new IOException("Для модели нужен защищённый HTTPS");
            HttpURLConnection connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(20000);
            connection.setReadTimeout(45000);
            connection.setInstanceFollowRedirects(false);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("User-Agent", "ChitsMarket/1.1");
            int statusCode = connection.getResponseCode();
            if (statusCode == 301 || statusCode == 302 || statusCode == 303 || statusCode == 307 || statusCode == 308) {
                String location = connection.getHeaderField("Location");
                connection.disconnect();
                if (location == null) throw new IOException("Пустое перенаправление модели");
                url = new URL(url, location);
                continue;
            }
            if (statusCode != 200) {
                connection.disconnect();
                throw new IOException("Репозиторий модели вернул HTTP " + statusCode);
            }
            try (InputStream in = connection.getInputStream(); ByteArrayOutputStream output = new ByteArrayOutputStream()) {
                byte[] buffer = new byte[8192]; int count;
                while ((count = in.read(buffer)) != -1) {
                    if (output.size() + count > JSON_LIMIT) throw new IOException("Ответ репозитория слишком большой");
                    output.write(buffer, 0, count);
                }
                return output.toString("UTF-8");
            } finally { connection.disconnect(); }
        }
        throw new IOException("Слишком много перенаправлений репозитория");
    }

    private void emit() {
        Listener current = listener;
        if (current != null) current.onEvent(object("type", "ai-model", "status", status,
            "progress", progress, "downloadedBytes", downloadedBytes, "totalBytes", totalBytes,
            "component", component, "error", error));
    }
    private static JSONObject object(Object... pairs) {
        JSONObject result = new JSONObject();
        try { for (int i = 0; i < pairs.length; i += 2) result.put((String) pairs[i], pairs[i + 1]); }
        catch (Exception impossible) { throw new IllegalArgumentException(impossible); }
        return result;
    }
    private static final class WaitingForWifi extends IOException { }
}
