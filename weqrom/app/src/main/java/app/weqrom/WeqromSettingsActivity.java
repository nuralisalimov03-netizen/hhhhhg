package app.weqrom;

import android.app.Activity;
import android.os.Bundle;
import android.widget.FrameLayout;

/** A stable settings entry, also used by launcher shortcuts. */
public final class WeqromSettingsActivity extends Activity {
    public static final String SHOW_REGION = "show_region";

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
        Runnable closed = new Runnable() {
            @Override public void run() {
                if (!isFinishing() && !isDestroyed()) finish();
            }
        };
        if (getIntent() != null && getIntent().getBooleanExtra(SHOW_REGION, false)) {
            WeqromUi.showRegionSettings(this, closed);
        } else {
            WeqromUi.showSettings(this, closed);
        }
    }
}
