package app.weqrom;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.os.Bundle;
import android.widget.FrameLayout;

/** Ordinary launcher entry: the welcome does not depend on a TikTok hook. */
public final class WeqromLauncherActivity extends Activity {
    public static final String FEED_ACTIVITY = "com.ss.android.ugc.aweme.main.MainActivity";
    private boolean feedOpened;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        FrameLayout background = new FrameLayout(this);
        background.setBackgroundColor(0xff080d0b);
        setContentView(background);
        getWindow().setStatusBarColor(0xff080d0b);
        getWindow().setNavigationBarColor(0xff080d0b);
        WeqromUi.initialize(this);
        QuickSettingsEntry.register(getApplication());
        BootstrapStatus.tryInitializeBridge(this);
        showWelcomeIfNeeded();
    }

    @Override public void onResume() {
        super.onResume();
        // Opening Settings from a launcher shortcut can replace the welcome
        // panel while this Activity remains in the task underneath it.
        showWelcomeIfNeeded();
    }

    private void showWelcomeIfNeeded() {
        if (feedOpened || isFinishing() || isDestroyed()) return;
        WeqromUi.showWelcome(this, new Runnable() {
            @Override public void run() { openFeed(); }
        });
    }

    private void openFeed() {
        if (feedOpened || isFinishing() || isDestroyed()) return;
        feedOpened = true;
        try {
            Intent feed = new Intent().setClassName(this, FEED_ACTIVITY);
            feed.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            startActivity(feed);
            finish();
        } catch (RuntimeException failure) {
            feedOpened = false;
            new AlertDialog.Builder(this).setTitle("Weqrom")
                .setMessage("Не удалось открыть ленту. Настройки Weqrom и диагностика доступны отдельно.")
                .setPositiveButton("Настройки", new DialogInterface.OnClickListener() {
                    @Override public void onClick(DialogInterface dialog, int which) {
                        startActivity(new Intent(WeqromLauncherActivity.this, WeqromSettingsActivity.class));
                    }
                }).setNegativeButton("Закрыть", new DialogInterface.OnClickListener() {
                    @Override public void onClick(DialogInterface dialog, int which) { finish(); }
                }).show();
        }
    }
}
