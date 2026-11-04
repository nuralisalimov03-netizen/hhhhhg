package app.weqrom;

import android.content.Context;
import android.content.SharedPreferences;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

/** Local country for the checked HTTP common-parameter builder.
 *
 * This does not edit account information, responses, the physical SIM or IP.
 * Each rewrite copies the supplied map and changes only existing client-region
 * keys. Preferences are read once during initialization, never per request.
 */
public final class RequestRegion {
    private static final String DEFAULT_COUNTRY = "BY";
    private static volatile Selection selected = new Selection(RegionCatalog.find(DEFAULT_COUNTRY));
    private static boolean initialized;
    private static SharedPreferences ownPreferences;
    private static volatile boolean configurationUnavailable;
    private static final AtomicLong preparedMaps = new AtomicLong();
    private static final AtomicLong rewrittenMaps = new AtomicLong();
    private static final AtomicLong preparedUrls = new AtomicLong();
    private static final AtomicLong rewrittenUrls = new AtomicLong();
    private static final AtomicLong signedUrlsSkipped = new AtomicLong();
    private static volatile Observation lastRewrite;
    private static volatile Observation lastUrlRewrite;
    private static final String[] CLIENT_KEYS = {
        "region", "carrier_region", "op_region", "sys_region", "carrier_region_v2", "mcc_mnc"
    };

    private RequestRegion() { }

    /** One immutable snapshot keeps ISO country and its operator codes together. */
    private static final class Selection {
        final String country;
        final String mccMnc;
        final String mcc;
        Selection(RegionCatalog.Entry entry) {
            country = entry.iso;
            mccMnc = entry.mccMnc;
            mcc = mccMnc.substring(0, 3);
        }
        String value(int field) {
            return field < 4 ? country : field == 4 ? mcc : mccMnc;
        }
    }

    private static final class Observation {
        final String country;
        final int fields;
        Observation(String country, int fields) {
            this.country = country;
            this.fields = fields;
        }
    }

    /** Safe before Application binding; a null context does not lock defaults. */
    public static synchronized void initialize(Context context) {
        if (initialized || context == null) return;
        try {
            Context application = context.getApplicationContext();
            Context storage = application != null ? application : context;
            ownPreferences = storage.getSharedPreferences("weqrom", Context.MODE_PRIVATE);
            String country = canonical(read(ownPreferences, "region"));
            if (country == null) country = canonical(read(
                storage.getSharedPreferences("ttmod_settings", Context.MODE_PRIVATE), "selected_region_iso"));
            selected = new Selection(RegionCatalog.find(country != null ? country : DEFAULT_COUNTRY));
            configurationUnavailable = false;
            initialized = true;
        } catch (RuntimeException unavailable) {
            // Retain a valid current choice rather than fail the core request
            // builder or leak a storage exception/message into diagnostics.
            configurationUnavailable = true;
        }
    }

    private static String read(SharedPreferences preferences, String key) {
        try {
            return preferences.getString(key, null);
        } catch (ClassCastException malformedStoredValue) {
            return null;
        }
    }

    private static String canonical(String country) {
        if (country == null) return null;
        String code = country.trim().toUpperCase(Locale.ROOT);
        if (code.length() != 2) return null;
        RegionCatalog.Entry entry = RegionCatalog.find(code);
        return entry == null ? null : entry.iso;
    }

    /** Selection events update the cache and the canonical own preference.
     * The private SharedPreferences handle is retained, never its Context.
     */
    public static synchronized void select(String country) {
        String code = canonical(country);
        if (code == null) throw new IllegalArgumentException("Unsupported request country");
        selected = new Selection(RegionCatalog.find(code));
        if (ownPreferences != null) {
            try {
                if (!code.equals(read(ownPreferences, "region"))) {
                    ownPreferences.edit().putString("region", code).apply();
                }
                configurationUnavailable = false;
            } catch (RuntimeException unavailable) {
                // The request snapshot remains valid even if the selection
                // cannot be persisted right now. A later selection can retry.
                configurationUnavailable = true;
            }
        }
    }

    /** Called after the original mod's checked region setter has persisted.
     * Its argument is the source table's array index, not the legacy region id.
     */
    public static void onModRegionSelected(int index) {
        if (index < 0 || index >= RegionCatalog.all().size()) return;
        select(RegionCatalog.all().get(index).iso);
    }

    public static String country() { return selected.country; }

    /** Descriptor: (Ljava/util/Map;)Ljava/util/HashMap; used by the checked DEX patch. */
    @SuppressWarnings({"rawtypes", "unchecked"})
    public static HashMap rewrite(Map original) {
        if (original == null) return null;
        // One snapshot ensures country and MCC values agree if a setting changes
        // concurrently. Do not mutate a cached/unmodifiable caller-owned map.
        Selection choice = selected;
        HashMap copy = new HashMap(original);
        int fields = 0;
        for (int field = 0; field < CLIENT_KEYS.length; field++) {
            if (original.containsKey(CLIENT_KEYS[field])) {
                copy.put(CLIENT_KEYS[field], choice.value(field));
                fields |= 1 << field;
            }
        }
        preparedMaps.incrementAndGet();
        if (fields != 0) {
            rewrittenMaps.incrementAndGet();
            lastRewrite = new Observation(choice.country, fields);
        }
        return copy;
    }

    /** Rewrite existing client fields in the checked common-parameter URL builders.
     *
     * Only values of recognized query keys are replaced. All other original
     * bytes, including percent escapes, parameter order and fragments, survive.
     * A recognized query signature makes the entire operation a no-op: the
     * original signing/transport layer must retain authority over signed URLs.
     */
    public static void rewriteUrl(StringBuilder url) {
        if (url == null) return;
        preparedUrls.incrementAndGet();
        String original = url.toString();
        int question = original.indexOf('?');
        int fragment = original.indexOf('#');
        if (question < 0 || (fragment >= 0 && question > fragment)) return;
        int queryEnd = fragment < 0 ? original.length() : fragment;
        Selection choice = selected;
        StringBuilder result = null;
        int copiedUntil = 0;
        int fields = 0;
        for (int start = question + 1; start < queryEnd;) {
            int end = original.indexOf('&', start);
            if (end < 0 || end > queryEnd) end = queryEnd;
            int equal = original.indexOf('=', start);
            if (equal < 0 || equal > end) equal = end;
            String key = decodeKey(original, start, equal);
            if (isSigningKey(key)) {
                signedUrlsSkipped.incrementAndGet();
                return;
            }
            int field = fieldIndex(key);
            if (field >= 0) {
                if (result == null) result = new StringBuilder(original.length() + 16);
                int valueStart = equal < end ? equal + 1 : equal;
                result.append(original, copiedUntil, valueStart);
                if (equal == end) result.append('=');
                result.append(choice.value(field));
                copiedUntil = end;
                fields |= 1 << field;
            }
            start = end + 1;
        }
        if (result == null) return;
        result.append(original, copiedUntil, original.length());
        url.replace(0, url.length(), result.toString());
        rewrittenUrls.incrementAndGet();
        lastUrlRewrite = new Observation(choice.country, fields);
    }

    private static int fieldIndex(String key) {
        if (key != null) {
            for (int index = 0; index < CLIENT_KEYS.length; index++) {
                if (CLIENT_KEYS[index].equals(key)) return index;
            }
        }
        return -1;
    }

    /** Single ASCII percent decode, confined to a query key, never its value. */
    private static String decodeKey(String value, int start, int end) {
        // Every recognized name is shorter than 32 ASCII characters; even fully
        // escaped known names fit here. Avoid allocating for arbitrary long keys.
        if (end - start > 96) return null;
        StringBuilder key = new StringBuilder(end - start);
        for (int index = start; index < end; index++) {
            char ch = value.charAt(index);
            if (ch == '%') {
                if (index + 2 >= end) return null;
                int high = hex(value.charAt(index + 1));
                int low = hex(value.charAt(index + 2));
                if (high < 0 || low < 0) return null;
                ch = (char) ((high << 4) | low);
                index += 2;
            }
            if (ch > 127) return null;
            key.append(ch);
        }
        return key.toString();
    }

    private static int hex(char ch) {
        if (ch >= '0' && ch <= '9') return ch - '0';
        if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
        if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
        return -1;
    }

    private static boolean isSigningKey(String key) {
        if (key == null) return false;
        String normalized = key.toLowerCase(Locale.ROOT);
        return normalized.equals("x-bogus") || normalized.equals("x-gnarly")
            || normalized.equals("_signature") || normalized.equals("x-amz-signature")
            || normalized.equals("x-goog-signature") || normalized.equals("signature");
    }

    /** Local observations only, not proof of a sent request or live server region.
     * The optional Context reads only the persisted server-routing country. It
     * cannot initialize/synchronize selection or write any preference.
     */
    public static String summary(Context context) {
        String country = selected.country;
        Observation last = lastRewrite;
        Observation lastUrl = lastUrlRewrite;
        StringBuilder report = new StringBuilder("Регион параметров клиента: ").append(country)
            .append("\nПодготовлено карт параметров: ").append(preparedMaps.get())
            .append("\nКарт с заменой региона: ").append(rewrittenMaps.get());
        if (last == null) report.append("\nПоля региона ещё не обрабатывались.");
        else report.append("\nПоследняя локальная замена: ").append(last.country)
            .append(" [").append(fieldNames(last.fields)).append(']');
        report.append("\nПодготовлено URL: ").append(preparedUrls.get())
            .append("\nURL с заменой региона: ").append(rewrittenUrls.get())
            .append("\nПодписанных URL сохранено: ").append(signedUrlsSkipped.get());
        if (lastUrl != null) report.append("\nПоследняя замена в URL: ").append(lastUrl.country)
            .append(" [").append(fieldNames(lastUrl.fields)).append(']');
        String storeCountry = persistedStoreCountry(context);
        report.append("\nПоследний сохранённый store_region: ")
            .append(storeCountry != null ? storeCountry : "неизвестно")
            .append("\nЭто локальное сохранённое значение, а не проверка текущего региона на сервере.");
        if (configurationUnavailable) report.append("\nНастройки пока не удалось синхронизировать; сохранён текущий выбор.");
        report.append("\nЭто параметры клиента, а не страна аккаунта или IP.");
        return report.toString();
    }

    /** Only a two-letter country is reportable; do not expose any other state. */
    private static String persistedStoreCountry(Context context) {
        if (context == null) return null;
        try {
            Context application = context.getApplicationContext();
            Context storage = application != null ? application : context;
            String country = read(storage.getSharedPreferences("ttnet_store_region", Context.MODE_PRIVATE), "store_region");
            if (country == null || country.length() != 2) return null;
            for (int index = 0; index < country.length(); index++) {
                char ch = country.charAt(index);
                if (!((ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z'))) return null;
            }
            // This routing diagnostic deliberately accepts RU, which is absent
            // from the source mod's selectable-country table.
            return country.toUpperCase(Locale.ROOT);
        } catch (RuntimeException unavailable) {
            return null;
        }
    }

    private static String fieldNames(int fields) {
        StringBuilder names = new StringBuilder();
        for (int field = 0; field < CLIENT_KEYS.length; field++) {
            if ((fields & (1 << field)) != 0) {
                if (names.length() != 0) names.append(", ");
                names.append(CLIENT_KEYS[field]);
            }
        }
        return names.toString();
    }
}
