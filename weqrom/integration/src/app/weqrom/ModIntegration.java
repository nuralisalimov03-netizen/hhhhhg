package app.weqrom;

import Z.ac;
import Z.bc;
import Z.f3;
import Z.te;
import Z.ue;
import android.app.Activity;
import android.content.Context;
import android.content.ContextWrapper;
import android.content.Intent;
import android.content.SharedPreferences;
import android.util.Log;
import android.view.View;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

/** Checked bridge to the particular TikTok You 47.4.3 mod bundled in this APK.
 * It changes the mod's SIM/operator country; it does not change a TikTok
 * account's server-side country, the SIM itself, GPS, or the public IP address.
 */
public final class ModIntegration {
    private static final String MOD_PREFERENCES = "ttmod_settings";
    private static final String MIGRATION = "weqrom_region_schema";
    private static final int SCHEMA = 2;
    private static final int DEFAULT_COLOR = 0xffff385d;
    private static volatile boolean themeAvailable;

    private ModIntegration() {}

    public static final class Region {
        public final int index;
        public final String iso;
        public final String name;
        public final String operator;

        private Region(int index, te region) {
            this.index = index;
            this.iso = region.d.toUpperCase(Locale.ROOT);
            this.name = region.c;
            this.operator = region.e;
        }
    }

    /** Main.start calls this immediately after ac.a(Context), before SIM hooks. */
    public static synchronized void initialize(Context context) {
        ac.a(context);
        bc settings = ac.e();
        SharedPreferences mod = context.getSharedPreferences(MOD_PREFERENCES, Context.MODE_PRIVATE);
        if (mod.getInt(MIGRATION, 0) < SCHEMA) {
            int belarus = indexOf("BY");
            // Use the existing real setter so its in-memory cache changes too.
            settings.c(belarus);
            settings.e(false);
            settings.m(false);
            // Keep migration and its stored country in one atomic preferences edit.
            mod.edit().putInt("selected_region", belarus)
                .putString("selected_region_iso", "by")
                .putBoolean("region_spoof_enabled", true)
                .putBoolean("auto_updates_enable", false)
                .putBoolean("statistics_enable", false)
                .putInt(MIGRATION, SCHEMA).apply();
        }
        WeqromUi.initialize(context);
        SharedPreferences own = WeqromUi.preferences(context);
        // The new welcome must also appear for users who accepted the old popup.
        if (!own.getBoolean("welcome_complete", false)) {
            settings.H = false;
            mod.edit().putBoolean("startup_consent_accepted", false).apply();
        }
        String selected = selectedRegionIso();
        int color = settings.r == 0 ? DEFAULT_COLOR : settings.r;
        if (!selected.equals(own.getString("region", "")) || color != own.getInt("like_color", 0)) {
            own.edit().putString("region", selected).putInt("like_color", color).apply();
        }
        themeAvailable = FeedTheme.available(context);
        WeqromUi.setIntegration(BACKEND);
    }

    private static final WeqromUi.Integration BACKEND = new WeqromUi.Integration() {
        public void apply(Activity activity, WeqromUi.Options options) {
            setRegion(activity, options.region);
            setAccent(activity, options.likeColor);
            setTheme(activity, options.theme);
        }

        public void openAdvanced(Activity activity) { ModIntegration.openAdvanced(activity); }

        public boolean hasRegionHook() { return true; }
        public boolean hasThemeHook() { return themeAvailable; }
        public boolean hasLikeColorHook() { return true; }
    };

    public static List<Region> regions() {
        ArrayList<Region> result = new ArrayList<Region>();
        for (int i = 0; i < ue.a.length; i++) result.add(new Region(i, ue.a[i]));
        return Collections.unmodifiableList(result);
    }

    private static int indexOf(String iso) {
        if (iso == null) throw new IllegalArgumentException("Country is required");
        for (int i = 0; i < ue.a.length; i++) {
            if (iso.equalsIgnoreCase(ue.a[i].d)) return i;
        }
        throw new IllegalArgumentException("Country is not supported by this mod: " + iso);
    }

    private static String selectedRegionIso() {
        int index = ac.e().b;
        if (index < 0 || index >= ue.a.length) index = indexOf("BY");
        return ue.a[index].d.toUpperCase(Locale.ROOT);
    }

    public static String selectedRegionIso(Context context) {
        initialize(context);
        return selectedRegionIso();
    }

    public static void setRegion(Context context, String iso) {
        int index = indexOf(iso);
        if (ac.e().b != index) ac.e().c(index);
        SharedPreferences own = WeqromUi.preferences(context);
        String normalized = ue.a[index].d.toUpperCase(Locale.ROOT);
        if (!normalized.equals(own.getString("region", ""))) {
            own.edit().putString("region", normalized).apply();
        }
    }

    public static String theme(Context context) {
        return WeqromUi.preferences(context).getString("theme", "dark");
    }

    public static void setTheme(Context context, String value) {
        if (!"dark".equals(value) && !"light".equals(value) && !"system".equals(value)) {
            throw new IllegalArgumentException("Unknown theme");
        }
        SharedPreferences own = WeqromUi.preferences(context);
        if (!value.equals(own.getString("theme", ""))) own.edit().putString("theme", value).apply();
        if (context instanceof Activity && themeAvailable && WeqromUi.isWelcomeComplete(context)) {
            if (!FeedTheme.apply((Activity) context, value)) {
                themeAvailable = false;
                throw new IllegalStateException("Host feed could not apply the selected theme");
            }
        }
    }

    public static int accent(Context context) {
        initialize(context);
        return ac.e().r == 0 ? DEFAULT_COLOR : ac.e().r;
    }

    public static void setAccent(Context context, int color) {
        if ((color >>> 24) != 255) throw new IllegalArgumentException("Accent must be opaque");
        bc settings = ac.e();
        if (settings.r != color) settings.a(color);
        if (!settings.q) settings.f(true);
        SharedPreferences own = WeqromUi.preferences(context);
        if (own.getInt("like_color", 0) != color) own.edit().putInt("like_color", color).apply();
    }

    public static void showWelcome(final Activity activity) {
        initialize(activity);
        WeqromUi.showWelcome(activity, new Runnable() {
            public void run() {
                // Original Main already initialized the recommendation feed.
                // Mark only local startup completion; do not run the old updater.
                ac.e().b();
            }
        });
    }

    public static void showSettings(Activity activity) {
        if (activity == null || activity.isFinishing() || activity.isDestroyed()) return;
        initialize(activity);
        WeqromUi.showSettings(activity);
    }

    /** Keep the source mod's existing optional settings available explicitly. */
    public static void openAdvanced(Activity activity) {
        if (activity == null || activity.isFinishing() || activity.isDestroyed()) return;
        activity.startActivity(new Intent().setClassName(activity, "Z.sf"));
    }

    /** Called by the original injected settings-row click handler. */
    public static void showSettingsFromArgs(Object[] arguments) {
        Activity activity = null;
        if (arguments != null) {
            for (Object argument : arguments) {
                if (argument instanceof Context) activity = activity((Context) argument);
                else if (argument instanceof View) activity = activity(((View) argument).getContext());
                if (activity != null) break;
            }
        }
        if (activity == null && f3.f != null) {
            Object latest = f3.f.get();
            if (latest instanceof Activity) activity = (Activity) latest;
        }
        if (activity == null) {
            Log.w("Weqrom", "No active host activity for settings");
            return;
        }
        showSettings(activity);
    }

    private static Activity activity(Context context) {
        // Bound the unwrap so a broken ContextWrapper cannot loop forever.
        for (int i = 0; i < 16 && context != null; i++) {
            if (context instanceof Activity) return (Activity) context;
            if (!(context instanceof ContextWrapper)) return null;
            Context next = ((ContextWrapper) context).getBaseContext();
            if (next == context) return null;
            context = next;
        }
        return null;
    }
}
