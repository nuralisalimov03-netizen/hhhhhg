package app.weqrom;

import android.app.Application;
import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;

/** Registers the in-app settings button when Android restores the feed directly.
 * This non-exported provider has no stored/queryable data or network work.
 */
public final class WeqromInitializer extends ContentProvider {
    @Override public boolean onCreate() {
        try {
            Context context = getContext();
            Context application = context == null ? null : context.getApplicationContext();
            if (application instanceof Application) {
                QuickSettingsEntry.register((Application) application);
            }
        } catch (RuntimeException unavailable) {
            // The normal launcher/settings entries also register the button.
        }
        return true;
    }

    @Override public Cursor query(Uri uri, String[] projection, String selection,
            String[] selectionArgs, String sortOrder) { return null; }
    @Override public String getType(Uri uri) { return null; }
    @Override public Uri insert(Uri uri, ContentValues values) { return null; }
    @Override public int delete(Uri uri, String selection, String[] selectionArgs) { return 0; }
    @Override public int update(Uri uri, ContentValues values, String selection,
            String[] selectionArgs) { return 0; }
}
