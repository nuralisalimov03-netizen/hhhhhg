package app.weqrom.ai;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.JavascriptInterface;
import android.webkit.MimeTypeMap;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;
import org.json.JSONObject;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/** Standalone offline assistant. Only packaged pages can receive its bridge. */
public final class MainActivity extends Activity {
    private static final String ORIGIN = "https://app.weqrom.ai/";
    private static final int EXPORT = 21;
    // The model and its cache live at process scope. All Activity instances must
    // share ordering across recreation; an old Activity must not free a new one.
    private static final ExecutorService ENGINE_WORKER = Executors.newSingleThreadExecutor();
    private final ExecutorService files = Executors.newSingleThreadExecutor();
    private final AtomicBoolean busy = new AtomicBoolean();
    private final AtomicBoolean cancelled = new AtomicBoolean();
    private final AtomicBoolean exportBusy = new AtomicBoolean();
    private volatile String state = "loading", model = "Weqrom Seed", error = "";
    private volatile long parameters;
    private volatile boolean destroyed;
    private WebView web;
    private File pendingExport;

    @Override public void onCreate(Bundle bundle) {
        super.onCreate(bundle);
        if (bundle != null) {
            String filename = bundle.getString("pendingExport");
            if (filename != null && filename.matches("[0-9a-f-]{36}\\.html")) {
                File saved = new File(new File(getCacheDir(), "exports"), filename);
                if (saved.isFile() && saved.length() <= 1024 * 1024) { pendingExport = saved; exportBusy.set(true); }
            }
        }
        getWindow().setStatusBarColor(Color.rgb(17, 20, 26));
        getWindow().setNavigationBarColor(Color.rgb(17, 20, 26));
        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(17, 20, 26));
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setBlockNetworkLoads(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setSupportMultipleWindows(false);
        web.addJavascriptInterface(new NativeBridge(), "WeqromNative");
        web.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String path = uri.getPath();
                if (!local(uri) || path == null || path.contains("..") || path.contains("\\") || path.indexOf('\0') >= 0 || path.startsWith("/model/")) return denied();
                if (path.equals("/")) path = "/index.html";
                try {
                    String extension = MimeTypeMap.getFileExtensionFromUrl(path);
                    String mime = extension.equals("js") ? "application/javascript" : MimeTypeMap.getSingleton().getMimeTypeFromExtension(extension);
                    return new WebResourceResponse(mime == null ? "application/octet-stream" : mime, "UTF-8", getAssets().open(path.substring(1)));
                } catch (IOException missing) { return denied(); }
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) { return !local(request.getUrl()); }
            @Override public void onPageFinished(WebView view, String url) { emit(stateEvent()); }
        });
        FrameLayout root = new FrameLayout(this);
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(), insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets.consumeSystemWindowInsets();
        });
        setContentView(root);
        web.loadUrl(ORIGIN);
        ENGINE_WORKER.execute(() -> {
            try {
                JSONObject metadata = ModelInstaller.metadata(getApplicationContext());
                model = metadata.getString("name"); parameters = metadata.getLong("parameters");
                emit(stateEvent());
                File file = ModelInstaller.restore(getApplicationContext(), metadata);
                if (destroyed) return;
                if (!OfflineAi.load(file)) throw new IOException("Встроенная модель не загрузилась.");
                state = "ready";
            } catch (Exception failure) { error = message(failure); state = "error"; }
            emit(stateEvent());
        });
    }

    private static boolean local(Uri uri) {
        return "https".equals(uri.getScheme()) && "app.weqrom.ai".equals(uri.getHost()) && uri.getPort() == -1 && uri.getUserInfo() == null;
    }
    private static WebResourceResponse denied() {
        return new WebResourceResponse("text/plain", "UTF-8", 403, "Offline", Collections.emptyMap(), new ByteArrayInputStream(new byte[0]));
    }
    private static JSONObject object(Object... pairs) {
        JSONObject data = new JSONObject();
        try { for (int index = 0; index < pairs.length; index += 2) data.put((String) pairs[index], pairs[index + 1]); }
        catch (Exception ignored) { }
        return data;
    }
    private JSONObject stateEvent() {
        JSONObject event = object("type", "state", "state", state, "model", model, "parameters", parameters);
        try { if (!error.isEmpty()) event.put("error", error).put("message", error); } catch (Exception ignored) { }
        return event;
    }
    private void emit(JSONObject data) {
        if (destroyed) return;
        runOnUiThread(() -> {
            if (!destroyed && web != null) web.evaluateJavascript("window.WeqromEvents&&window.WeqromEvents.onEvent(" + data.toString() + ")", null);
        });
    }
    private static String message(Exception failure) {
        String text = failure.getMessage();
        return text == null || text.isEmpty() ? "Не удалось выполнить операцию." : text.substring(0, Math.min(400, text.length()));
    }
    private void notice(String text) { runOnUiThread(() -> { if (!destroyed) Toast.makeText(this, text, Toast.LENGTH_LONG).show(); }); }

    public final class NativeBridge {
        @JavascriptInterface public String status() { return stateEvent().toString(); }
        @JavascriptInterface public void generate(String request) {
            final PromptFormatter formatted;
            try { formatted = PromptFormatter.parse(request); }
            catch (Exception invalid) {
                String id = "";
                try { if (request != null && request.length() <= 65536) id = new JSONObject(request).optString("id", ""); } catch (Exception ignored) { }
                emit(object("id", id, "type", "error", "message", message(invalid)));
                return;
            }
            if (!state.equals("ready")) { emit(object("id", formatted.id, "type", "error", "message", state.equals("error") ? error : "Модель ещё подготавливается.")); return; }
            if (!busy.compareAndSet(false, true)) { emit(object("id", formatted.id, "type", "error", "message", "Дождитесь текущего ответа.")); return; }
            cancelled.set(false);
            long epoch = OfflineAi.epoch();
            ENGINE_WORKER.execute(() -> {
                try {
                    String answer = cancelled.get() ? "" : OfflineAi.generate(formatted.prompt, formatted.maxTokens, epoch,
                        text -> emit(object("id", formatted.id, "type", "partial", "text", text)));
                    emit(object("id", formatted.id, "type", "done", "text", answer, "cancelled", cancelled.get()));
                } catch (Exception failure) { emit(object("id", formatted.id, "type", "error", "message", message(failure))); }
                finally { busy.set(false); }
            });
        }
        @JavascriptInterface public void cancel() { cancelled.set(true); OfflineAi.cancel(); }
        @JavascriptInterface public void previewHtml(String html) {
            final byte[] content;
            try { content = htmlBytes(html); } catch (Exception invalid) { notice(message(invalid)); return; }
            files.execute(() -> {
                try {
                    File directory = new File(getCacheDir(), "previews");
                    if (!directory.isDirectory() && !directory.mkdirs()) throw new IOException("Не удалось подготовить просмотр.");
                    File file = new File(directory, UUID.randomUUID().toString() + ".html");
                    try (OutputStream output = new FileOutputStream(file)) { output.write(content); }
                    runOnUiThread(() -> { if (!destroyed) startActivity(new Intent(MainActivity.this, PreviewActivity.class).putExtra("file", file.getName())); });
                } catch (Exception failure) { notice(message(failure)); }
            });
        }
        @JavascriptInterface public void exportHtml(String html, String filename) {
            final byte[] content;
            try { content = htmlBytes(html); } catch (Exception invalid) { notice(message(invalid)); return; }
            String name = filename == null ? "weqrom-game.html" : filename.replaceAll("[^A-Za-z0-9А-Яа-яЁё._ -]", "_");
            if (name.length() > 80) name = name.substring(0, 80);
            if (!name.toLowerCase(java.util.Locale.ROOT).endsWith(".html")) name += ".html";
            final String title = name;
            if (!exportBusy.compareAndSet(false, true)) { notice("Сначала завершите сохранение текущего файла."); return; }
            files.execute(() -> {
                try {
                    File directory = new File(getCacheDir(), "exports");
                    if (!directory.isDirectory() && !directory.mkdirs()) throw new IOException("Не удалось подготовить экспорт.");
                    File file = new File(directory, UUID.randomUUID().toString() + ".html");
                    try (OutputStream output = new FileOutputStream(file)) { output.write(content); }
                    runOnUiThread(() -> {
                        if (destroyed) { file.delete(); exportBusy.set(false); return; }
                        pendingExport = file;
                        try { startActivityForResult(new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("text/html").putExtra(Intent.EXTRA_TITLE, title), EXPORT); }
                        catch (Exception unavailable) { pendingExport = null; exportBusy.set(false); file.delete(); notice("На устройстве нет средства сохранения документов."); }
                    });
                } catch (Exception failure) { exportBusy.set(false); notice(message(failure)); }
            });
        }
    }
    private static byte[] htmlBytes(String html) throws IOException {
        if (html == null || html.trim().isEmpty() || html.length() > 1024 * 1024) throw new IOException("HTML пустой или слишком большой.");
        byte[] bytes = html.getBytes(StandardCharsets.UTF_8);
        if (bytes.length > 1024 * 1024) throw new IOException("HTML превышает 1 МиБ.");
        return bytes;
    }
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != EXPORT) return;
        final File content = pendingExport; pendingExport = null; exportBusy.set(false);
        if (result != RESULT_OK || data == null || data.getData() == null || content == null) { if (content != null) content.delete(); return; }
        final Uri destination = data.getData();
        files.execute(() -> {
            try (FileInputStream input = new FileInputStream(content); OutputStream output = getContentResolver().openOutputStream(destination, "wt")) {
                if (output == null) throw new IOException("Не удалось открыть выбранный файл.");
                byte[] buffer = new byte[65536]; int count; long total = 0;
                while ((count = input.read(buffer)) != -1) {
                    total += count; if (total > 1024 * 1024) throw new IOException("HTML превышает 1 МиБ.");
                    output.write(buffer, 0, count);
                }
                notice("HTML сохранён.");
            } catch (Exception failure) { notice(message(failure)); }
            finally { content.delete(); }
        });
    }
    @Override protected void onSaveInstanceState(Bundle bundle) {
        if (pendingExport != null) bundle.putString("pendingExport", pendingExport.getName());
        super.onSaveInstanceState(bundle);
    }
    @Override protected void onDestroy() {
        destroyed = true;
        cancelled.set(true); OfflineAi.cancel();
        // Retain the small native model for the next Activity. Process exit frees
        // it, and serialized startup reuses only the same verified model stamp.
        files.shutdown();
        if (web != null) { web.removeJavascriptInterface("WeqromNative"); web.destroy(); web = null; }
        super.onDestroy();
    }
}
