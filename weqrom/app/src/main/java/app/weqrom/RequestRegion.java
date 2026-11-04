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
    private static volatile String selected = DEFAULT_COUNTRY;
    private static boolean initialized;
    private static SharedPreferences ownPreferences;
    private static volatile boolean configurationUnavailable;
    private static final AtomicLong preparedMaps = new AtomicLong();
    private static final AtomicLong rewrittenMaps = new AtomicLong();
    private static volatile Observation lastRewrite;

    private RequestRegion() { }

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
            selected = country != null ? country : DEFAULT_COUNTRY;
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
        selected = code;
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

    public static String country() { return selected; }

    /** Descriptor: (Ljava/util/Map;)Ljava/util/HashMap; used by the checked DEX patch. */
    @SuppressWarnings({"rawtypes", "unchecked"})
    public static HashMap rewrite(Map original) {
        if (original == null) return null;
        // One snapshot ensures all three parameters agree if a setting changes
        // concurrently. Do not mutate a cached/unmodifiable caller-owned map.
        String country = selected;
        HashMap copy = new HashMap(original);
        int fields = 0;
        if (original.containsKey("region")) {
            copy.put("region", country);
            fields |= 1;
        }
        if (original.containsKey("carrier_region")) {
            copy.put("carrier_region", country);
            fields |= 2;
        }
        if (original.containsKey("op_region")) {
            copy.put("op_region", country);
            fields |= 4;
        }
        preparedMaps.incrementAndGet();
        if (fields != 0) {
            rewrittenMaps.incrementAndGet();
            lastRewrite = new Observation(country, fields);
        }
        return copy;
    }

    /** Local observations only, not proof of a sent request or server region.
     * Context is intentionally not read here: reporting cannot change settings.
     */
    public static String summary(Context context) {
        String country = selected;
        Observation last = lastRewrite;
        StringBuilder report = new StringBuilder("Регион параметров клиента: ").append(country)
            .append("\nПодготовлено карт параметров: ").append(preparedMaps.get())
            .append("\nКарт с заменой региона: ").append(rewrittenMaps.get());
        if (last == null) report.append("\nПоля региона ещё не обрабатывались.");
        else report.append("\nПоследняя локальная замена: ").append(last.country)
            .append(" [").append(fieldNames(last.fields)).append(']');
        if (configurationUnavailable) report.append("\nНастройки пока не удалось синхронизировать; сохранён текущий выбор.");
        report.append("\nЭто параметры клиента, а не страна аккаунта или IP.");
        return report.toString();
    }

    private static String fieldNames(int fields) {
        StringBuilder names = new StringBuilder();
        if ((fields & 1) != 0) names.append("region");
        if ((fields & 2) != 0) {
            if (names.length() != 0) names.append(", ");
            names.append("carrier_region");
        }
        if ((fields & 4) != 0) {
            if (names.length() != 0) names.append(", ");
            names.append("op_region");
        }
        return names.toString();
    }
}
