package market.chits;

import android.content.Context;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Matrix;
import android.media.ExifInterface;
import android.net.Uri;
import android.provider.OpenableColumns;
import android.util.Base64;
import org.json.JSONObject;
import java.io.*;
import java.util.UUID;

/** One explicit, private photo attachment. Originals and EXIF are never retained. */
final class AssistantPhotos {
    private final Context context;
    private final File photo;
    private volatile JSONObject selected;
    AssistantPhotos(Context context) {
        this.context=context;
        photo=new File(context.getCacheDir(),"chits-chat-photo.jpg");
        // A cold start discards prior attachments rather than retaining private photos.
        photo.delete();
    }
    JSONObject state(){return selected;}
    File file(){return selected!=null && photo.isFile()?photo:null;}
    void clear(){selected=null;photo.delete();}
    JSONObject importPhoto(Uri uri)throws Exception{
        File original=File.createTempFile("chits-photo-source",".tmp",context.getCacheDir());
        File normalized=File.createTempFile("chits-photo-scaled",".jpg",context.getCacheDir());
        Bitmap decoded=null,rotated=null,scaled=null;
        try{
            try(InputStream in=context.getContentResolver().openInputStream(uri);OutputStream out=new FileOutputStream(original)){
                if(in==null)throw new IOException("Не удалось открыть фото");
                byte[] buffer=new byte[65536];long total=0;int n;
                while((n=in.read(buffer))!=-1){total+=n;if(total>25L*1024*1024)throw new IOException("Выберите фото размером до 25 МБ");out.write(buffer,0,n);}
            }
            BitmapFactory.Options bounds=new BitmapFactory.Options();bounds.inJustDecodeBounds=true;
            BitmapFactory.decodeFile(original.getAbsolutePath(),bounds);
            if(bounds.outWidth<=0||bounds.outHeight<=0||(long)bounds.outWidth*bounds.outHeight>150000000L)throw new IOException("Не удалось прочитать фото. Попробуйте JPEG, PNG или WebP");
            BitmapFactory.Options options=new BitmapFactory.Options();options.inSampleSize=1;
            while(Math.max(bounds.outWidth,bounds.outHeight)/options.inSampleSize>1120)options.inSampleSize*=2;
            options.inPreferredConfig=Bitmap.Config.ARGB_8888;
            decoded=BitmapFactory.decodeFile(original.getAbsolutePath(),options);
            if(decoded==null)throw new IOException("Это изображение не поддерживается");
            Matrix matrix=new Matrix();
            try{
                int orientation=new ExifInterface(original.getAbsolutePath()).getAttributeInt(ExifInterface.TAG_ORIENTATION,ExifInterface.ORIENTATION_NORMAL);
                switch(orientation){
                    case ExifInterface.ORIENTATION_FLIP_HORIZONTAL:matrix.setScale(-1,1);break;
                    case ExifInterface.ORIENTATION_ROTATE_180:matrix.setRotate(180);break;
                    case ExifInterface.ORIENTATION_FLIP_VERTICAL:matrix.setScale(1,-1);break;
                    case ExifInterface.ORIENTATION_TRANSPOSE:matrix.setRotate(90);matrix.postScale(-1,1);break;
                    case ExifInterface.ORIENTATION_ROTATE_90:matrix.setRotate(90);break;
                    case ExifInterface.ORIENTATION_TRANSVERSE:matrix.setRotate(-90);matrix.postScale(-1,1);break;
                    case ExifInterface.ORIENTATION_ROTATE_270:matrix.setRotate(-90);break;
                    default:break;
                }
            }catch(IOException ignored){ }
            rotated=Bitmap.createBitmap(decoded,0,0,decoded.getWidth(),decoded.getHeight(),matrix,true);
            float factor=Math.min(1f,560f/Math.max(rotated.getWidth(),rotated.getHeight()));
            scaled=Bitmap.createScaledBitmap(rotated,Math.max(1,Math.round(rotated.getWidth()*factor)),Math.max(1,Math.round(rotated.getHeight()*factor)),true);
            try(OutputStream out=new FileOutputStream(normalized)){
                if(!scaled.compress(Bitmap.CompressFormat.JPEG,88,out))throw new IOException("Не удалось подготовить фото");
            }
            String name="Фото.jpg";
            try(Cursor cursor=context.getContentResolver().query(uri,new String[]{OpenableColumns.DISPLAY_NAME},null,null,null)){
                if(cursor!=null&&cursor.moveToFirst()&&cursor.getString(0)!=null)name=cursor.getString(0).replaceAll("[\\p{Cntrl}]","");
            }catch(Exception ignored){ }
            if(name.length()>100)name=name.substring(0,100);
            ByteArrayOutputStream bytes=new ByteArrayOutputStream();
            try(InputStream in=new FileInputStream(normalized)){byte[] buffer=new byte[8192];int n;while((n=in.read(buffer))!=-1)bytes.write(buffer,0,n);}
            JSONObject metadata=new JSONObject().put("id",UUID.randomUUID().toString()).put("name",name).put("width",scaled.getWidth()).put("height",scaled.getHeight()).put("preview","data:image/jpeg;base64,"+Base64.encodeToString(bytes.toByteArray(),Base64.NO_WRAP));
            if(!normalized.renameTo(photo))throw new IOException("Не удалось сохранить вложение");
            selected=metadata;
            return metadata;
        }finally{
            original.delete();normalized.delete();
            if(scaled!=null&&scaled!=rotated&&scaled!=decoded)scaled.recycle();
            if(rotated!=null&&rotated!=decoded)rotated.recycle();
            if(decoded!=null)decoded.recycle();
        }
    }
}
