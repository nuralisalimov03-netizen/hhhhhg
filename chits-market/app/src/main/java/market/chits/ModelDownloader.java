package market.chits;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InterruptedIOException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Verified, resumable model transfer. No Android or third-party dependencies. */
public final class ModelDownloader {
    public interface Progress {
        /** Throw IOException to pause/cancel while retaining the downloaded prefix. */
        void onProgress(long current, long total) throws IOException;
    }

    private static final Pattern RANGE = Pattern.compile("bytes ([0-9]+)-([0-9]+)/([0-9]+)");
    private static final int BUFFER_SIZE = 64 * 1024;
    private ModelDownloader() { }

    /**
     * Leaves a verified complete file at partial, ready for the caller's atomic rename.
     * Transport failures and cancellation retain an incomplete prefix. A failed hash or
     * oversized body removes the untrusted file, so it can never become an installed model.
     */
    public static void download(URL url, File partial, long expectedSize,
                                String expectedSha256, long limit, Progress progress) throws IOException {
        if (expectedSize <= 0 || limit <= 0 || expectedSize > limit)
            throw new IOException("Invalid or excessive model size");
        if (expectedSha256 == null || !expectedSha256.matches("[a-fA-F0-9]{64}"))
            throw new IOException("Model SHA-256 is missing or invalid");
        checkURL(url, false);
        byte[] expectedHash = decodeHex(expectedSha256);
        File parent = partial.getAbsoluteFile().getParentFile();
        if (parent != null && !parent.isDirectory() && !parent.mkdirs())
            throw new IOException("Cannot create model download directory");
        if (partial.exists() && !partial.isFile()) throw new IOException("Partial model is not a regular file");
        long offset = partial.isFile() ? partial.length() : 0;
        try {
            if (offset > expectedSize) throw new IntegrityException("Partial model exceeds its expected size");
            if (offset == expectedSize) {
                verify(partial, expectedSize, expectedHash);
                notifyProgress(progress, offset, expectedSize);
                return;
            }
            checkInterrupted();
            HttpURLConnection connection = connect(url, offset);
            try {
                int status = connection.getResponseCode();
                boolean append;
                if (status == HttpURLConnection.HTTP_PARTIAL) {
                    checkRange(connection, offset, expectedSize);
                    append = offset > 0;
                } else if (status == HttpURLConnection.HTTP_OK) {
                    // Servers which ignore Range must restart, never append a full body.
                    long length = connection.getContentLengthLong();
                    if (length >= 0 && length != expectedSize)
                        throw new IOException("HTTP length differs from model metadata");
                    offset = 0;
                    append = false;
                } else if (status == 416 && partial.length() == expectedSize) {
                    // Covers a file completed by another local owner while connecting.
                    verify(partial, expectedSize, expectedHash);
                    notifyProgress(progress, expectedSize, expectedSize);
                    return;
                } else {
                    throw new IOException("Model server returned HTTP " + status);
                }
                String encoding = connection.getHeaderField("Content-Encoding");
                if (encoding != null && !encoding.trim().isEmpty() && !"identity".equalsIgnoreCase(encoding))
                    throw new IOException("Encoded model responses cannot be resumed safely");
                MessageDigest hash = sha256();
                if (append) hashPrefix(partial, offset, hash);
                long total = offset;
                try (InputStream input = connection.getInputStream();
                     FileOutputStream output = new FileOutputStream(partial, append)) {
                    notifyProgress(progress, total, expectedSize);
                    byte[] buffer = new byte[BUFFER_SIZE];
                    int count;
                    while ((count = input.read(buffer)) != -1) {
                        checkInterrupted();
                        if (count > expectedSize - total || count > limit - total)
                            throw new IntegrityException("Downloaded model exceeds expected size");
                        output.write(buffer, 0, count);
                        hash.update(buffer, 0, count);
                        total += count;
                        notifyProgress(progress, total, expectedSize);
                    }
                    output.getFD().sync();
                }
                if (total != expectedSize)
                    throw new IOException("Model download interrupted; saved prefix can be resumed");
                if (!MessageDigest.isEqual(hash.digest(), expectedHash))
                    throw new IntegrityException("Model SHA-256 verification failed");
            } finally {
                connection.disconnect();
            }
        } catch (IntegrityException invalid) {
            if (partial.exists() && !partial.delete())
                throw new IOException(invalid.getMessage() + "; cannot remove invalid partial file", invalid);
            throw invalid;
        }
    }

    private static HttpURLConnection connect(URL original, long offset) throws IOException {
        URL current = original;
        boolean httpsOnly = "https".equalsIgnoreCase(original.getProtocol());
        for (int redirects = 0; redirects <= 8; redirects++) {
            checkInterrupted();
            checkURL(current, httpsOnly);
            HttpURLConnection connection = (HttpURLConnection) current.openConnection();
            connection.setConnectTimeout(20000);
            connection.setReadTimeout(45000);
            connection.setInstanceFollowRedirects(false);
            connection.setRequestProperty("User-Agent", "ChitsMarket/1.1");
            connection.setRequestProperty("Accept-Encoding", "identity");
            if (offset > 0) connection.setRequestProperty("Range", "bytes=" + offset + "-");
            final int status;
            try {
                status = connection.getResponseCode();
            } catch (IOException failure) {
                connection.disconnect();
                throw failure;
            }
            if (status == 301 || status == 302 || status == 303 || status == 307 || status == 308) {
                String location = connection.getHeaderField("Location");
                connection.disconnect();
                if (location == null || redirects == 8) throw new IOException("Invalid model redirect chain");
                URL next = new URL(current, location);
                // An HTTPS hop must never downgrade, including to loopback HTTP.
                if ("https".equalsIgnoreCase(current.getProtocol())) httpsOnly = true;
                checkURL(next, httpsOnly);
                current = next;
            } else {
                return connection;
            }
        }
        throw new IOException("Too many model redirects");
    }

    private static void checkURL(URL url, boolean httpsOnly) throws IOException {
        if (url == null || url.getUserInfo() != null || url.getHost().isEmpty())
            throw new IOException("Invalid model URL");
        String protocol = url.getProtocol();
        boolean https = "https".equalsIgnoreCase(protocol);
        boolean localTest = "http".equalsIgnoreCase(protocol)
                && ("localhost".equalsIgnoreCase(url.getHost()) || "127.0.0.1".equals(url.getHost()));
        if (!https && (!localTest || httpsOnly))
            throw new IOException("Models require HTTPS; TLS downgrade refused");
    }

    private static void checkRange(HttpURLConnection connection, long offset, long expected) throws IOException {
        String value = connection.getHeaderField("Content-Range");
        Matcher match = RANGE.matcher(value == null ? "" : value.trim());
        if (!match.matches()) throw new IOException("Missing or invalid HTTP Content-Range");
        try {
            long start = Long.parseLong(match.group(1));
            long end = Long.parseLong(match.group(2));
            long total = Long.parseLong(match.group(3));
            if (start != offset || end != expected - 1 || total != expected || end < start)
                throw new IOException("HTTP Content-Range differs from requested model range");
            long length = connection.getContentLengthLong();
            if (length >= 0 && length != expected - offset)
                throw new IOException("Partial HTTP length differs from Content-Range");
        } catch (NumberFormatException failure) {
            throw new IOException("HTTP Content-Range is out of bounds", failure);
        }
    }

    private static MessageDigest sha256() throws IOException {
        try { return MessageDigest.getInstance("SHA-256"); }
        catch (NoSuchAlgorithmException impossible) { throw new IOException("SHA-256 is unavailable", impossible); }
    }

    private static void hashPrefix(File file, long expectedLength, MessageDigest hash) throws IOException {
        byte[] buffer = new byte[BUFFER_SIZE];
        long length = 0;
        try (InputStream input = new FileInputStream(file)) {
            int count;
            while ((count = input.read(buffer)) != -1) {
                checkInterrupted();
                if (count > expectedLength - length) throw new IntegrityException("Partial model changed during download");
                hash.update(buffer, 0, count);
                length += count;
            }
        }
        if (length != expectedLength) throw new IOException("Partial model changed during download");
    }

    private static void verify(File file, long expectedSize, byte[] expectedHash) throws IOException {
        MessageDigest hash = sha256();
        hashPrefix(file, expectedSize, hash);
        if (!MessageDigest.isEqual(hash.digest(), expectedHash))
            throw new IntegrityException("Model SHA-256 verification failed");
    }

    private static byte[] decodeHex(String input) {
        byte[] bytes = new byte[32];
        for (int i = 0; i < bytes.length; i++) bytes[i] = (byte) Integer.parseInt(input.substring(i * 2, i * 2 + 2), 16);
        return bytes;
    }

    private static void notifyProgress(Progress callback, long current, long total) throws IOException {
        if (callback != null) callback.onProgress(current, total);
    }

    private static void checkInterrupted() throws InterruptedIOException {
        if (Thread.currentThread().isInterrupted()) throw new InterruptedIOException("Model download paused");
    }

    private static final class IntegrityException extends IOException {
        private static final long serialVersionUID = 1L;
        IntegrityException(String message) { super(message); }
    }
}
