package market.chits;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.File;
import java.io.FileNotFoundException;

/** A read-only, temporary URI grant to one explicitly selected private APK. */
public final class ApkProvider extends ContentProvider {
    public boolean onCreate() { return true; }
    private File resolve(Uri uri) throws FileNotFoundException {
        if (uri.getPathSegments().size() != 2 || !"apk".equals(uri.getPathSegments().get(0))) throw new FileNotFoundException();
        String id = uri.getLastPathSegment();
        if (id == null || !id.matches("[a-zA-Z0-9_-]{1,100}")) throw new FileNotFoundException();
        File file = new File(getContext().getFilesDir(), "apks/" + id + ".apk");
        if (!file.isFile()) throw new FileNotFoundException();
        return file;
    }
    public String getType(Uri uri) { return "application/vnd.android.package-archive"; }
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode)) throw new FileNotFoundException("Read only");
        return ParcelFileDescriptor.open(resolve(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }
    public Cursor query(Uri uri, String[] projection, String selection, String[] args, String sort) {
        try {
            File file = resolve(uri);
            MatrixCursor cursor = new MatrixCursor(new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE});
            cursor.addRow(new Object[]{file.getName(), file.length()});
            return cursor;
        } catch (FileNotFoundException e) { return null; }
    }
    public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    public int delete(Uri uri, String selection, String[] args) { throw new UnsupportedOperationException(); }
    public int update(Uri uri, ContentValues values, String selection, String[] args) { throw new UnsupportedOperationException(); }
}
