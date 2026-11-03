package market.chits;

import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;
import java.util.Locale;
import java.util.UUID;

/**
 * Narrow native transport for the same-phone Termux API. WebView's HTTPS
 * document never needs cross-address-space HTTP permission. This class neither
 * accesses arbitrary URLs nor forwards arbitrary browser headers.
 */
public final class LocalApiTransport {
    private static final long APK_LIMIT = 200L * 1024 * 1024;
    private static final int RESPONSE_LIMIT = 4 * 1024 * 1024;
    private static final int JSON_LIMIT = 16384;
    private static final int TIMEOUT_MS = 20000;
    private LocalApiTransport() {}

    public static JSONObject execute(String baseUrl, JSONObject request, File upload, String uploadName) throws Exception {
        URI base;
        try { base = new URI(baseUrl == null ? "" : baseUrl.trim()); }
        catch (Exception error) { throw new IOException("Некорректный локальный адрес сервера"); }
        String host = base.getHost();
        if (!"http".equalsIgnoreCase(base.getScheme()) || host == null ||
                !(host.equalsIgnoreCase("localhost") || host.equals("127.0.0.1") || host.equals("10.0.2.2")) ||
                base.getRawUserInfo() != null || base.getRawQuery() != null || base.getRawFragment() != null ||
                !(base.getRawPath() == null || base.getRawPath().isEmpty() || base.getRawPath().equals("/")) ||
                base.getPort() == 0 || base.getPort() > 65535) {
            throw new IOException("Локальный транспорт разрешён только для HTTP localhost/127.0.0.1 (10.0.2.2 в эмуляторе)");
        }
        if (request == null) throw new IOException("Пустой запрос к локальному серверу");
        String method = request.optString("method", "GET");
        String path = request.optString("path", "");
        boolean jsonGet = method.equals("GET") && (path.equals("/health") || path.equals("/api/apps"));
        boolean jsonPost = method.equals("POST") && (path.equals("/api/auth/login") || path.equals("/api/auth/logout"));
        boolean multipart = method.equals("POST") && path.equals("/api/apps");
        boolean delete = method.equals("DELETE") && path.matches("/api/apps/[A-Za-z0-9_-]{1,100}");
        if (!(jsonGet || jsonPost || multipart || delete)) throw new IOException("Этот маршрут локального API запрещён");

        JSONObject headers = request.optJSONObject("headers");
        if (request.has("headers") && !request.isNull("headers") && headers == null) throw new IOException("Некорректные заголовки запроса");
        String authorization = null;
        if (headers != null) {
            Iterator<String> names = headers.keys();
            while (names.hasNext()) {
                String name = names.next();
                if (!name.equalsIgnoreCase("Authorization")) throw new IOException("Локальный API принимает только заголовок Authorization");
                if (authorization != null) throw new IOException("Повторяющийся заголовок Authorization");
                Object value = headers.get(name);
                if (!(value instanceof String)) throw new IOException("Некорректный ключ доступа");
                authorization = (String)value;
                if (!authorization.matches("Bearer [A-Za-z0-9._~+-]{1,4096}")) throw new IOException("Некорректный ключ доступа");
            }
        }

        byte[] body = null, prefix = null, suffix = null;
        String boundary = null;
        long fileLength = 0;
        if (multipart) {
            if (upload == null || !upload.isFile() || !upload.canRead()) throw new IOException("Выберите APK-файл перед публикацией");
            fileLength = upload.length();
            if (fileLength <= 0 || fileLength > APK_LIMIT) throw new IOException("APK должен быть от 1 байта до 200 МБ");
            JSONObject fields = request.optJSONObject("fields");
            if (fields == null) throw new IOException("Не указаны поля публикации APK");
            Iterator<String> names = fields.keys();
            while (names.hasNext()) {
                String name = names.next();
                if (!(name.equals("title") || name.equals("description") || name.equals("category"))) throw new IOException("Неизвестное поле публикации APK");
            }
            boundary = "ChitsLocal" + UUID.randomUUID().toString().replace("-", "");
            ByteArrayOutputStream text = new ByteArrayOutputStream();
            for (String name : new String[]{"title", "description", "category"}) {
                if (!fields.has(name)) continue;
                Object value = fields.get(name);
                if (!(value instanceof String)) throw new IOException("Поля публикации должны быть текстом");
                byte[] field = ((String)value).getBytes(StandardCharsets.UTF_8);
                if (field.length > 20000) throw new IOException("Текстовое поле публикации слишком большое");
                text.write(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name + "\"\r\n\r\n").getBytes(StandardCharsets.UTF_8));
                text.write(field);
                text.write(new byte[]{13, 10});
            }
            String filename = uploadName == null ? "upload.apk" : new File(uploadName).getName();
            filename = filename.replaceAll("[\\x00-\\x1f\\x7f\"\\\\]", "");
            if (filename.isEmpty()) filename = "upload.apk";
            if (filename.length() > 180) filename = filename.substring(0, 180);
            text.write(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" + filename + "\"\r\nContent-Type: application/vnd.android.package-archive\r\n\r\n").getBytes(StandardCharsets.UTF_8));
            prefix = text.toByteArray();
            suffix = ("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8);
            if ((long)prefix.length + suffix.length > 65536) throw new IOException("Заголовки публикации слишком большие");
        } else if (jsonPost) {
            Object value = request.opt("body");
            if (value != null && value != JSONObject.NULL && !(value instanceof String)) throw new IOException("Тело запроса должно быть строкой JSON");
            body = (value == null || value == JSONObject.NULL ? "" : (String)value).getBytes(StandardCharsets.UTF_8);
            if (body.length > JSON_LIMIT) throw new IOException("JSON-запрос слишком большой");
        } else if (upload != null) {
            throw new IOException("Файл допустим только при публикации APK");
        }

        // Normalize localhost to a numeric loopback address: no DNS lookup can
        // turn the trusted local endpoint into an external destination.
        URL target = new URL("http", host.equalsIgnoreCase("localhost") ? "127.0.0.1" : host, base.getPort(), path);
        HttpURLConnection connection = (HttpURLConnection)target.openConnection();
        connection.setRequestMethod(method);
        connection.setConnectTimeout(TIMEOUT_MS);
        connection.setReadTimeout(TIMEOUT_MS);
        connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("User-Agent", "ChitsMarket/LocalApi");
        if (authorization != null) connection.setRequestProperty("Authorization", authorization);
        try {
            if (multipart) {
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
                connection.setFixedLengthStreamingMode((long)prefix.length + fileLength + suffix.length);
                try (OutputStream output = connection.getOutputStream(); InputStream input = new FileInputStream(upload)) {
                    output.write(prefix);
                    byte[] buffer = new byte[65536];
                    long copied = 0;
                    int count;
                    while ((count = input.read(buffer)) != -1) {
                        copied += count;
                        if (copied > fileLength) throw new IOException("Размер APK изменился во время публикации");
                        output.write(buffer, 0, count);
                    }
                    if (copied != fileLength) throw new IOException("Передача APK не завершена");
                    output.write(suffix);
                }
            } else if (body != null) {
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                // JSON is bounded at 16 KiB. Let HttpURLConnection buffer it:
                // fixed streaming mode on the JRE discards some 401 bodies as
                // non-retryable authentication responses. APKs stay streamed.
                try (OutputStream output = connection.getOutputStream()) { output.write(body); }
            }
            int status = connection.getResponseCode();
            if (status >= 300 && status < 400) throw new IOException("Перенаправления локального API запрещены");
            String contentType = connection.getContentType();
            if (contentType == null || !contentType.toLowerCase(Locale.ROOT).startsWith("application/json")) throw new IOException("Локальный сервер вернул ответ без JSON");
            if (connection.getContentLengthLong() > RESPONSE_LIMIT) throw new IOException("Ответ локального сервера слишком большой");
            InputStream stream = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
            if (stream == null) {
                if (status >= 400) {
                    JSONObject error = new JSONObject();
                    error.put("error", status == 401 ? "Сессия недействительна. Войдите снова" : "Локальный сервер вернул HTTP " + status);
                    JSONObject result = new JSONObject();
                    result.put("statusCode", status);
                    result.put("body", error);
                    return result;
                }
                throw new IOException("Локальный сервер вернул пустой ответ");
            }
            ByteArrayOutputStream data = new ByteArrayOutputStream();
            try (InputStream input = stream) {
                byte[] buffer = new byte[16384];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    if ((long)data.size() + count > RESPONSE_LIMIT) throw new IOException("Ответ локального сервера слишком большой");
                    data.write(buffer, 0, count);
                }
            }
            JSONObject result = new JSONObject();
            result.put("statusCode", status);
            result.put("body", new JSONObject(new String(data.toByteArray(), StandardCharsets.UTF_8)));
            return result;
        } finally {
            connection.disconnect();
        }
    }
}
