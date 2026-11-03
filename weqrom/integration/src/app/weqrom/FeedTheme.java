package app.weqrom;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;
import java.lang.ref.WeakReference;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;

/**
 * Uses the theme preference and notifier of the pinned TikTok 47.4.3 input.
 *
 * Its ThemeSettingPage uses sp_dark_mode/dark_mode, and its click listener calls
 * X.005C.LJJIII(int) before recreating activities. X.005C is that APK's relocated
 * AppCompatDelegate. The binary name starts with a digit, hence exact reflection
 * rather than a Java import. No guessed class names or view-colour polling.
 */
public final class FeedTheme {
    private static final String PREFS = "sp_dark_mode";
    private static final String KEY = "dark_mode";
    private static Method notifier;
    private static int revision;

    private FeedTheme() { }

    private static synchronized Method notifier(Context context) throws ReflectiveOperationException {
        if (notifier == null) {
            Class<?> owner = Class.forName("X.005C", false, context.getClassLoader());
            Method candidate = owner.getMethod("LJJIII", Integer.TYPE);
            if (!Modifier.isStatic(candidate.getModifiers()) || candidate.getReturnType() != Void.TYPE) {
                throw new NoSuchMethodException("Pinned theme notifier signature differs");
            }
            notifier = candidate;
        }
        return notifier;
    }

    public static boolean available(Context context) {
        if (context == null) return false;
        try {
            notifier(context);
            return true;
        } catch (ReflectiveOperationException failure) {
            return false;
        } catch (LinkageError failure) {
            return false;
        } catch (SecurityException failure) {
            return false;
        }
    }

    /** Call for an explicit user change on the main thread, after dismissing settings. */
    public static boolean apply(Activity activity, String theme) {
        if (activity == null || activity.isFinishing() || activity.isDestroyed()) return false;
        final int value;
        if ("system".equals(theme)) value = -1;
        else if ("light".equals(theme)) value = 1;
        else if ("dark".equals(theme)) value = 2;
        else return false;
        if (Looper.myLooper() != Looper.getMainLooper()) return false;
        SharedPreferences preferences = activity.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        boolean existed = preferences.contains(KEY);
        int previous = preferences.getInt(KEY, 1);
        if (previous == value) return available(activity);
        try {
            Method method = notifier(activity);
            preferences.edit().putInt(KEY, value).apply();
            method.invoke(null, Integer.valueOf(value));
        } catch (ReflectiveOperationException failure) {
            restore(preferences, existed, previous);
            return false;
        } catch (LinkageError failure) {
            restore(preferences, existed, previous);
            return false;
        } catch (SecurityException failure) {
            restore(preferences, existed, previous);
            return false;
        }
        // The original notifier updates registered AppCompat delegates. Some
        // host activities also need recreation, as in the original settings UI.
        // One cancellable-by-revision post is used; there is no recurring work.
        final int change = ++revision;
        final WeakReference<Activity> owner = new WeakReference<Activity>(activity);
        new Handler(Looper.getMainLooper()).postDelayed(new Runnable() {
            @Override public void run() {
                Activity current = owner.get();
                if (change == revision && current != null
                        && !current.isFinishing() && !current.isDestroyed()) {
                    current.recreate();
                }
            }
        }, 240L);
        return true;
    }

    private static void restore(SharedPreferences preferences, boolean existed, int previous) {
        SharedPreferences.Editor editor = preferences.edit();
        if (existed) editor.putInt(KEY, previous);
        else editor.remove(KEY);
        editor.apply();
    }
}
