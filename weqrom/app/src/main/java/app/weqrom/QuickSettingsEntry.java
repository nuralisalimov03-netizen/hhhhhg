package app.weqrom;

import android.app.Activity;
import android.app.Application;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ShortcutInfo;
import android.content.pm.ShortcutManager;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.Icon;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.widget.FrameLayout;
import android.widget.TextView;
import java.util.ArrayList;
import java.util.List;

/** A small in-app button and two standard launcher shortcuts; no service,
 * system overlay, permissions, polling, or dependency on mod-row injection.
 */
public final class QuickSettingsEntry implements Application.ActivityLifecycleCallbacks {
    private static final String TAG = "weqrom.quick.settings.v3";
    private static final String SETTINGS_ID = "weqrom_settings";
    private static final String REGION_ID = "weqrom_regions";
    private static boolean registered;

    private QuickSettingsEntry() { }

    public static synchronized void register(Application application) {
        if (application == null || registered) return;
        application.registerActivityLifecycleCallbacks(new QuickSettingsEntry());
        registered = true;
        publishShortcuts(application);
    }

    private static int dp(Context context, int value) {
        return Math.round(context.getResources().getDisplayMetrics().density * value);
    }

    @Override public void onActivityResumed(final Activity activity) {
        if (!WeqromLauncherActivity.FEED_ACTIVITY.equals(activity.getClass().getName())
                || activity.isFinishing() || activity.isDestroyed()) return;
        View decor = activity.getWindow().getDecorView();
        if (!(decor instanceof FrameLayout) || decor.findViewWithTag(TAG) != null) return;
        final FrameLayout host = (FrameLayout) decor;
        final FrameLayout touch = new FrameLayout(activity);
        touch.setTag(TAG);
        touch.setContentDescription("Настройки Weqrom");
        touch.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
        TextView button = new TextView(activity);
        button.setText("W");
        button.setTextSize(18);
        button.setTypeface(Typeface.create("sans-serif", Typeface.BOLD));
        button.setTextColor(0xff9bff75);
        button.setGravity(Gravity.CENTER);
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(0xee080d0b);
        shape.setCornerRadius(dp(activity, 12));
        shape.setStroke(dp(activity, 1), 0xff51754b);
        button.setBackground(shape);
        button.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        touch.addView(button, new FrameLayout.LayoutParams(dp(activity, 36), dp(activity, 36), Gravity.CENTER));
        touch.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View view) {
                activity.startActivity(new Intent(activity, WeqromSettingsActivity.class));
            }
        });
        final FrameLayout.LayoutParams position = new FrameLayout.LayoutParams(dp(activity, 48), dp(activity, 48), Gravity.TOP | Gravity.LEFT);
        position.leftMargin = dp(activity, 8);
        position.topMargin = dp(activity, 8);
        touch.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener() {
            @Override public WindowInsets onApplyWindowInsets(View view, WindowInsets insets) {
                position.leftMargin = dp(activity, 8) + insets.getSystemWindowInsetLeft();
                position.topMargin = dp(activity, 8) + insets.getSystemWindowInsetTop();
                view.setLayoutParams(position);
                return insets;
            }
        });
        touch.setElevation(dp(activity, 20));
        host.addView(touch, position);
        touch.requestApplyInsets();
    }

    private static void publishShortcuts(Context context) {
        if (Build.VERSION.SDK_INT < 26) return;
        try {
            ShortcutManager manager = context.getSystemService(ShortcutManager.class);
            Intent launch = context.getPackageManager().getLaunchIntentForPackage(context.getPackageName());
            ComponentName owner = launch == null ? null : launch.getComponent();
            if (manager == null || owner == null || manager.isRateLimitingActive()) return;
            int maximum = manager.getMaxShortcutCountPerActivity();
            if (maximum < 2) return;
            ArrayList<ShortcutInfo> shortcuts = new ArrayList<ShortcutInfo>();
            shortcuts.add(shortcut(context, owner, SETTINGS_ID, "Настройки", false, 0));
            shortcuts.add(shortcut(context, owner, REGION_ID, "Регионы", true, 1));
            // Retain other client shortcuts up to the launcher's per-activity
            // limit rather than replacing all of them with the Weqrom entries.
            int retainedForOwner = 0;
            List<ShortcutInfo> previous = manager.getDynamicShortcuts();
            for (ShortcutInfo item : previous) {
                if (SETTINGS_ID.equals(item.getId()) || REGION_ID.equals(item.getId())) continue;
                if (owner.equals(item.getActivity())) {
                    if (retainedForOwner >= maximum - 2) continue;
                    retainedForOwner++;
                }
                shortcuts.add(item);
            }
            manager.setDynamicShortcuts(shortcuts);
        } catch (RuntimeException unsupportedLauncher) {
            // Some launchers disable shortcuts; the visible in-app W button
            // remains independent of this optional OS capability.
        }
    }

    private static ShortcutInfo shortcut(Context context, ComponentName owner, String id, String label, boolean region, int rank) {
        Intent target = new Intent(context, WeqromSettingsActivity.class).setAction(Intent.ACTION_VIEW);
        target.putExtra(WeqromSettingsActivity.SHOW_REGION, region);
        return new ShortcutInfo.Builder(context, id).setActivity(owner).setShortLabel(label)
            .setLongLabel("Weqrom · " + label).setIcon(icon()).setIntent(target).setRank(rank).build();
    }

    private static Icon icon() {
        Bitmap bitmap = Bitmap.createBitmap(96, 96, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(bitmap);
        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        paint.setColor(0xff080d0b);
        canvas.drawRoundRect(0, 0, 96, 96, 24, 24, paint);
        paint.setColor(0xff9bff75);
        paint.setTextSize(58);
        paint.setTypeface(Typeface.create("sans-serif", Typeface.BOLD));
        paint.setTextAlign(Paint.Align.CENTER);
        canvas.drawText("W", 48, 68, paint);
        return Icon.createWithBitmap(bitmap);
    }

    @Override public void onActivityDestroyed(Activity activity) {
        View decor = activity.getWindow().getDecorView();
        View entry = decor.findViewWithTag(TAG);
        if (entry != null && entry.getParent() instanceof ViewGroup) ((ViewGroup) entry.getParent()).removeView(entry);
    }

    @Override public void onActivityCreated(Activity activity, Bundle state) { }
    @Override public void onActivityStarted(Activity activity) { }
    @Override public void onActivityPaused(Activity activity) { }
    @Override public void onActivityStopped(Activity activity) { }
    @Override public void onActivitySaveInstanceState(Activity activity, Bundle state) { }
}
