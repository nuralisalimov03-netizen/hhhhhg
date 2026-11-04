package app.weqrom.ai;

import android.app.Activity;
import android.os.Bundle;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;

/** Untrusted generated code runs here, with no JavaScript-to-Java interface. */
public final class PreviewActivity extends Activity {
    private static final String DOCUMENT = "https://preview.weqrom.invalid/index.html";
    private WebView web;
    private volatile File source;
    private volatile byte[] document;
    private volatile boolean destroyed;
    @Override public void onCreate(Bundle bundle) {
        super.onCreate(bundle);
        web = new WebView(this);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setBlockNetworkLoads(true);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setSupportMultipleWindows(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(true);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) { return true; }
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                byte[] content = document;
                if (request.isForMainFrame() && DOCUMENT.equals(request.getUrl().toString()) && content != null) {
                    Map<String, String> headers = new HashMap<>();
                    headers.put("Content-Security-Policy", PreviewPolicy.CSP);
                    headers.put("Cache-Control", "no-store");
                    headers.put("X-Content-Type-Options", "nosniff");
                    headers.put("Referrer-Policy", "no-referrer");
                    return new WebResourceResponse("text/html", "UTF-8", 200, "OK", headers, new ByteArrayInputStream(content));
                }
                return new WebResourceResponse("text/plain", "UTF-8", 403, "Offline preview", Collections.emptyMap(), new ByteArrayInputStream(new byte[0]));
            }
        });
        web.setWebChromeClient(new WebChromeClient());
        FrameLayout root = new FrameLayout(this);
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(), insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets.consumeSystemWindowInsets();
        });
        setContentView(root);
        new Thread(() -> {
            try {
                File directory = new File(getCacheDir(), "previews").getCanonicalFile();
                String name = getIntent().getStringExtra("file");
                if (name == null || !name.matches("[0-9a-f-]{36}\\.html")) throw new java.io.IOException();
                source = new File(directory, name).getCanonicalFile();
                if (!directory.equals(source.getParentFile()) || !source.isFile() || source.length() > 1024 * 1024) throw new java.io.IOException();
                try (FileInputStream input = new FileInputStream(source); ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
                    byte[] buffer = new byte[8192]; int count;
                    while ((count = input.read(buffer)) != -1) {
                        if (bytes.size() + count > 1024 * 1024) throw new java.io.IOException();
                        bytes.write(buffer, 0, count);
                    }
                    document = bytes.toByteArray();
                }
                runOnUiThread(() -> { if (!destroyed) web.loadUrl(DOCUMENT); });
            } catch (Exception failure) { runOnUiThread(() -> { if (!destroyed) { Toast.makeText(this, "Не удалось открыть HTML.", Toast.LENGTH_LONG).show(); finish(); } }); }
        }, "weqrom-preview").start();
    }
    @Override protected void onDestroy() {
        destroyed = true;
        if (web != null) { web.destroy(); web = null; }
        if (source != null && isFinishing()) source.delete();
        super.onDestroy();
    }
}
