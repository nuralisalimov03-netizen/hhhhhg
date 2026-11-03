package app.weqrom;

import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import java.lang.reflect.InvocationTargetException;
import java.util.IdentityHashMap;

/** Nonthrowing, local diagnostics for the optional original mod's startup.
 * A successful Main.start return does not prove every hook was installed.
 * Exception messages are deliberately never persisted or displayed.
 */
public final class BootstrapStatus {
    private static final String STORE = "weqrom_bootstrap";
    private static boolean attempted;
    private static boolean started;
    private static String startupFailure;
    private static String hooksFailure;
    private static long attemptTime;

    private BootstrapStatus() { }

    /** Optional explicit attempt boundary, safe during attachBaseContext. */
    public static synchronized void begin(Context context) {
        try {
            reset();
            save(context);
        } catch (Throwable ignored) { }
    }

    private static void reset() {
        attempted = true;
        started = false;
        startupFailure = null;
        hooksFailure = null;
        attemptTime = System.currentTimeMillis();
    }

    private static void ensureAttempt() {
        if (!attempted) reset();
    }

    /** Called only after the original reflective Main.start returned. */
    public static synchronized void started(Context context) {
        try {
            ensureAttempt();
            started = true;
            // A caught hook error from this attempt is retained, not hidden by
            // the loader's otherwise successful return.
            save(context);
        } catch (Throwable ignored) { }
    }

    public static synchronized void failed(Context context, Throwable failure) {
        try {
            ensureAttempt();
            started = false;
            startupFailure = describe(failure);
            save(context);
        } catch (Throwable ignored) { }
    }

    public static synchronized void hookError(Context context, Throwable failure) {
        try {
            ensureAttempt();
            hooksFailure = describe(failure);
            save(context);
        } catch (Throwable ignored) { }
    }

    public static synchronized boolean isStarted(Context context) {
        // A report from another process or a previous launch is not evidence
        // that this process successfully initialized the optional module.
        return attempted && started && startupFailure == null;
    }

    public static synchronized boolean hasHooksError(Context context) {
        return hooksFailure != null;
    }

    public static synchronized String failureSummary(Context context) {
        if (startupFailure != null) return "Не удалось запустить функции Weqrom. Подробности — в отчёте.";
        if (hooksFailure != null) return "Часть функций не включилась. Подробности — в отчёте.";
        if (!attempted || !started) return "Функции мода ещё не готовы. Выбор сохраняется на телефоне.";
        return "Модуль Weqrom загружен.";
    }

    public static synchronized String dump(Context context) {
        StringBuilder report = new StringBuilder("Weqrom · диагностика запуска\n");
        try {
            if (context != null) {
                PackageInfo info = context.getPackageManager().getPackageInfo(context.getPackageName(), 0);
                report.append("Версия: ").append(safeName(info.versionName, 100)).append('\n');
            }
        } catch (Throwable ignored) { }
        report.append("Попытка в текущем процессе: ").append(attempted ? "да" : "нет").append('\n');
        report.append("Main.start вернулся: ").append(isStarted(context) ? "да" : "нет").append('\n');
        report.append("Ошибка регистрации функций: ").append(hasHooksError(context) ? "да" : "нет").append('\n');
        report.append(failureSummary(context));
        if (startupFailure != null) report.append("\nОшибка запуска: ").append(startupFailure);
        if (hooksFailure != null) report.append("\nОшибка функций: ").append(hooksFailure);
        // Persisted data is explicitly history; never use it to report current
        // readiness. It helps when startup did not reach any recording hook.
        if (!attempted) {
            try {
                SharedPreferences previous = preferences(context);
                if (previous != null && previous.contains("time")) {
                    report.append("\nПредыдущий сохранённый запуск: ")
                        .append(previous.getBoolean("started", false) ? "Main.start вернулся" : "без подтверждения");
                    String old = previous.getString("failure", "");
                    String oldHooks = previous.getString("hooks", "");
                    if (!old.isEmpty()) report.append("\nПредыдущая ошибка: ").append(old);
                    if (!oldHooks.isEmpty()) report.append("\nПредыдущая ошибка функций: ").append(oldHooks);
                }
            } catch (Throwable ignored) { }
        }
        return report.toString();
    }

    /** Bind the real module only after its recorded startup succeeded.
     * No source-mod class appears in these parent-APK method descriptors.
     */
    public static boolean tryInitializeBridge(Context context) {
        if (!isStarted(context) || hasHooksError(context)) {
            WeqromUi.setIntegration(null);
            return false;
        }
        try {
            Class<?> bridge = Class.forName("app.weqrom.ModIntegration", true, context.getClassLoader());
            bridge.getMethod("initialize", Context.class).invoke(null, context);
            return true;
        } catch (Throwable failure) {
            Throwable actual = failure;
            if (failure instanceof InvocationTargetException && failure.getCause() != null) actual = failure.getCause();
            hookError(context, actual);
            WeqromUi.setIntegration(null);
            return false;
        }
    }

    private static SharedPreferences preferences(Context context) {
        if (context == null) return null;
        Context application = context.getApplicationContext();
        return (application != null ? application : context).getSharedPreferences(STORE, Context.MODE_PRIVATE);
    }

    private static void save(Context context) {
        SharedPreferences preferences = preferences(context);
        if (preferences == null) return;
        preferences.edit().putLong("time", attemptTime).putBoolean("started", started)
            .putString("failure", startupFailure == null ? "" : startupFailure)
            .putString("hooks", hooksFailure == null ? "" : hooksFailure).apply();
    }

    private static String describe(Throwable failure) {
        if (failure == null) return "неизвестный тип ошибки";
        IdentityHashMap<Throwable, Boolean> seen = new IdentityHashMap<Throwable, Boolean>();
        Throwable actual = failure;
        for (int i = 0; i < 8; i++) {
            if (seen.put(actual, Boolean.TRUE) != null) break;
            Throwable cause = actual.getCause();
            if (cause == null || seen.containsKey(cause)) break;
            actual = cause;
        }
        StringBuilder result = new StringBuilder(safeName(actual.getClass().getName(), 130));
        StackTraceElement[] frames = actual.getStackTrace();
        for (int i = 0; i < frames.length && i < 2; i++) {
            StackTraceElement frame = frames[i];
            result.append(" · ").append(safeName(frame.getClassName(), 120)).append('.')
                .append(safeName(frame.getMethodName(), 70));
            if (frame.getLineNumber() >= 0) result.append(':').append(frame.getLineNumber());
        }
        return result.toString();
    }

    private static String safeName(String value, int maximum) {
        if (value == null) return "?";
        String clean = value.replaceAll("[^A-Za-z0-9_.$<>+\\-]", "_");
        return clean.length() > maximum ? clean.substring(0, maximum) : clean;
    }
}
