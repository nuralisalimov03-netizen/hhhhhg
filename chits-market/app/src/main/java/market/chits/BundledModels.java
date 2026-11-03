package market.chits;

import android.content.Context;
import android.content.SharedPreferences;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.security.MessageDigest;
import java.util.HashSet;
import java.util.Set;

/** Restores only signed-APK model assets described by a strict checksum manifest. */
public final class BundledModels {
    public interface Listener { void onProgress(String role, long current, long total) throws IOException; }
    private static final long LIMIT = 8L * 1024 * 1024 * 1024;
    private static final long MARGIN = 64L * 1024 * 1024;
    private BundledModels() { }

    public static boolean hasRole(Context context, String role) {
        try {
            JSONObject manifest=manifest(context);
            if(manifest==null)return false;
            JSONArray files=manifest.getJSONArray("files");
            for(int i=0;i<files.length();i++)if(role.equals(files.getJSONObject(i).getString("role")))return true;
        }catch(Exception ignored){ }
        return false;
    }

    public static boolean isInstalledFromBundle(Context context,String role){
        try{
            JSONObject data=manifest(context);if(data==null)return false;
            JSONArray files=data.getJSONArray("files");SharedPreferences prefs=context.getSharedPreferences("bundled-models",Context.MODE_PRIVATE);
            for(int i=0;i<files.length();i++){
                JSONObject entry=files.getJSONObject(i);if(!role.equals(entry.getString("role")))continue;
                File file=new File(context.getFilesDir(),entry.getString("target"));
                return stampMatches(prefs,entry,file);
            }
        }catch(Exception ignored){ }
        return false;
    }

    /** Run on a worker. Unknown existing text imports and ready vision pairs are preserved. */
    public static synchronized boolean restore(Context context,Listener listener)throws Exception{
        JSONObject data=manifest(context);if(data==null)return false;
        return restoreSelected(context,listener,data);
    }

    /** Text startup never copies or loads the much larger vision profile. */
    public static synchronized boolean restoreText(Context context,Listener listener)throws Exception{
        JSONObject data=select(manifest(context),true);if(data==null)return false;
        return restoreSelected(context,listener,data);
    }

    /** Vision is restored only when its explicit mode is requested. */
    public static synchronized boolean restoreVision(Context context,Listener listener)throws Exception{
        JSONObject data=select(manifest(context),false);if(data==null)return false;
        return restoreSelected(context,listener,data);
    }

    private static JSONObject select(JSONObject manifest,boolean textOnly)throws Exception{
        if(manifest==null)return null;
        JSONArray all=manifest.getJSONArray("files"),selected=new JSONArray();
        for(int i=0;i<all.length();i++){
            JSONObject entry=all.getJSONObject(i);
            if(entry.getString("role").equals("text")==textOnly)selected.put(entry);
        }
        if(selected.length()==0)return null;
        JSONObject result=new JSONObject().put("schema",1).put("files",selected);
        if(manifest.has("visionRevision"))result.put("visionRevision",manifest.getString("visionRevision"));
        validateManifest(result);
        return result;
    }

    /** Called only under the shared class monitor held by all public restorers. */
    private static boolean restoreSelected(Context context,Listener listener,JSONObject data)throws Exception{
        JSONArray files=data.getJSONArray("files");
        SharedPreferences stamps=context.getSharedPreferences("bundled-models",Context.MODE_PRIVATE);
        SharedPreferences vision=context.getSharedPreferences("vision-model",Context.MODE_PRIVATE);
        File visionFile=new File(context.getFilesDir(),"assistant-vision.gguf");
        File projectorFile=new File(context.getFilesDir(),"assistant-mmproj.gguf");
        boolean preserveVision=vision.getBoolean("ready",false)
            &&visionFile.isFile()&&projectorFile.isFile()
            &&visionFile.length()==vision.getLong("modelBytes",-1)
            &&projectorFile.length()==vision.getLong("projectorBytes",-1);
        File[] candidates=new File[files.length()];boolean[] replace=new boolean[files.length()];
        long needed=0,additional=0;
        for(int i=0;i<files.length();i++){
            JSONObject entry=files.getJSONObject(i);String role=entry.getString("role");
            File target=new File(context.getFilesDir(),entry.getString("target"));
            candidates[i]=target;
            if(stampMatches(stamps,entry,target))continue;
            String currentHash=target.isFile()&&target.length()==entry.getLong("size")?digest(target):null;
            if(entry.getString("sha256").equals(currentHash)){stamp(stamps,entry,target);continue;}
            // Preserve a user-imported text model and an already functional vision pair.
            if(role.equals("text")&&target.isFile()){
                String previous=stamps.getString("sha-"+target.getName(),"");
                if(previous.isEmpty())continue;
                if(currentHash==null)currentHash=digest(target);
                if(!previous.equals(currentHash))continue;
            }
            if(!role.equals("text")&&preserveVision)continue;
            candidates[i]=new File(context.getFilesDir(),entry.getString("target")+".bundled-part");
            replace[i]=true;needed+=entry.getLong("size");
            additional+=Math.max(0,entry.getLong("size")-(candidates[i].isFile()?candidates[i].length():0));
        }
        if(needed==0)return true;
        if(context.getFilesDir().getUsableSpace()<additional+MARGIN)throw new IOException("Недостаточно места для распаковки встроенной модели");
        boolean complete=false;
        try{
            for(int i=0;i<files.length();i++)if(replace[i])copy(context,files.getJSONObject(i),candidates[i],listener);
            // Validate actual GGUFs before committing any newly restored model.
            synchronized(OfflineAi.class){
                try{
                    for(int i=0;i<files.length();i++)if(replace[i]&&files.getJSONObject(i).getString("role").equals("text")){
                        if(!OfflineAi.load(candidates[i].getAbsolutePath()))throw new IOException("Встроенная текстовая модель не загрузилась");
                        OfflineAi.release();
                    }
                    int language=index(files,"vision"),projector=index(files,"projector");
                    if(language>=0&&(replace[language]||replace[projector])){
                        if(!OfflineAi.load(candidates[language].getAbsolutePath(),candidates[projector].getAbsolutePath()))throw new IOException("Встроенная визуальная модель не загрузилась");
                    }
                }finally{OfflineAi.release();}
            }
            int language=index(files,"vision"),projector=index(files,"projector");
            boolean replaceVision=language>=0&&(replace[language]||replace[projector]);
            if(replaceVision&&!vision.edit().putBoolean("ready",false).commit())throw new IOException("Не удалось сохранить состояние зрения");
            for(int i=0;i<files.length();i++)if(replace[i]){
                JSONObject entry=files.getJSONObject(i);File target=new File(context.getFilesDir(),entry.getString("target"));
                if(!candidates[i].renameTo(target))throw new IOException("Не удалось установить встроенную модель");
                stamp(stamps,entry,target);
                if(entry.getString("role").equals("text"))context.getSharedPreferences("fast-model",Context.MODE_PRIVATE).edit().putString("source","bundled").putString("sha256",entry.getString("sha256")).putLong("bytes",entry.getLong("size")).commit();
            }
            if(replaceVision){
                JSONObject model=files.getJSONObject(language),projection=files.getJSONObject(projector);
                if(!vision.edit().putLong("modelBytes",model.getLong("size")).putLong("projectorBytes",projection.getLong("size"))
                    .putString("modelSha256",model.getString("sha256")).putString("projectorSha256",projection.getString("sha256"))
                    .putString("readyRevision",data.getString("visionRevision")).putBoolean("ready",true).commit())throw new IOException("Не удалось завершить установку встроенного зрения");
            }
            complete=true;return true;
        }finally{
            // Failed integrity/native validation must never publish partial assets.
            if(!complete)for(int i=0;i<candidates.length;i++)if(replace[i]&&candidates[i].getName().endsWith(".bundled-part"))candidates[i].delete();
        }
    }

    private static int index(JSONArray files,String role)throws Exception{
        for(int i=0;i<files.length();i++)if(role.equals(files.getJSONObject(i).getString("role")))return i;return -1;
    }
    private static boolean stampMatches(SharedPreferences prefs,JSONObject entry,File target)throws Exception{
        String name=target.getName();return target.isFile()&&target.length()==entry.getLong("size")
            &&entry.getString("sha256").equals(prefs.getString("sha-"+name,""))
            &&target.lastModified()==prefs.getLong("mtime-"+name,-1);
    }
    private static void stamp(SharedPreferences prefs,JSONObject entry,File file)throws Exception{
        if(!prefs.edit().putString("sha-"+file.getName(),entry.getString("sha256")).putLong("mtime-"+file.getName(),file.lastModified()).commit())throw new IOException("Не удалось сохранить проверку встроенного файла");
    }
    private static void copy(Context context,JSONObject entry,File output,Listener progress)throws Exception{
        MessageDigest all=MessageDigest.getInstance("SHA-256");long total=0;
        try(OutputStream out=new FileOutputStream(output)){
            JSONArray parts=entry.getJSONArray("parts");
            for(int i=0;i<parts.length();i++){
                JSONObject part=parts.getJSONObject(i);MessageDigest hash=MessageDigest.getInstance("SHA-256");long size=0;
                try(InputStream in=context.getAssets().open(part.getString("path"))){
                    byte[] buffer=new byte[65536];int n;
                    while((n=in.read(buffer))!=-1){size+=n;total+=n;if(size>part.getLong("size")||total>entry.getLong("size"))throw new IOException("Размер встроенной модели не совпал");out.write(buffer,0,n);hash.update(buffer,0,n);all.update(buffer,0,n);if(progress!=null)progress.onProgress(entry.getString("role"),total,entry.getLong("size"));}
                }
                if(size!=part.getLong("size")||!hex(hash.digest()).equals(part.getString("sha256")))throw new IOException("Нарушена целостность части встроенной модели");
            }
        }
        if(total!=entry.getLong("size")||!hex(all.digest()).equals(entry.getString("sha256")))throw new IOException("SHA-256 встроенной модели не совпал");
    }
    private static String digest(File file)throws Exception{
        MessageDigest hash=MessageDigest.getInstance("SHA-256");try(InputStream in=new FileInputStream(file)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)hash.update(b,0,n);}return hex(hash.digest());
    }
    private static String hex(byte[] value){StringBuilder out=new StringBuilder();for(byte b:value)out.append(String.format(java.util.Locale.ROOT,"%02x",b&255));return out.toString();}
    private static JSONObject manifest(Context context)throws Exception{
        String[] entries=context.getAssets().list("models");boolean exists=false;
        if(entries!=null)for(String name:entries)if(name.equals("manifest.json"))exists=true;
        if(!exists)return null;
        byte[] content;
        try(InputStream in=context.getAssets().open("models/manifest.json");ByteArrayOutputStream out=new ByteArrayOutputStream()){
            byte[] b=new byte[8192];int n;while((n=in.read(b))!=-1){if(out.size()+n>1024*1024)throw new IOException("Слишком большой манифест встроенных моделей");out.write(b,0,n);}content=out.toByteArray();
        }catch(FileNotFoundException absent){return null;}
        JSONObject data=new JSONObject(new String(content,"UTF-8"));validateManifest(data);
        Set<String> actual=new HashSet<>();for(String name:entries)actual.add(name);
        JSONArray files=data.getJSONArray("files");
        for(int i=0;i<files.length();i++){
            JSONArray parts=files.getJSONObject(i).getJSONArray("parts");
            for(int j=0;j<parts.length();j++)if(!actual.contains(parts.getJSONObject(j).getString("path").substring("models/".length())))throw new IOException("В APK отсутствует часть встроенной модели");
        }
        return data;
    }
    static void validateManifest(JSONObject data)throws Exception{
        if(data.getInt("schema")!=1)throw new IOException("Неверный формат встроенных моделей");
        JSONArray files=data.getJSONArray("files");if(files.length()<1||files.length()>3)throw new IOException("Неверное количество встроенных моделей");
        Set<String> roles=new HashSet<>(),paths=new HashSet<>();long total=0;
        for(int i=0;i<files.length();i++){
            JSONObject entry=files.getJSONObject(i);String role=entry.getString("role"),target=entry.getString("target");
            String expected=role.equals("text")?"assistant.gguf":role.equals("vision")?"assistant-vision.gguf":role.equals("projector")?"assistant-mmproj.gguf":"";
            long size=entry.getLong("size");if(expected.isEmpty()||!expected.equals(target)||!roles.add(role)||size<4||size>LIMIT||!entry.getString("sha256").matches("[0-9a-f]{64}"))throw new IOException("Некорректная встроенная модель");
            JSONArray parts=entry.getJSONArray("parts");if(parts.length()<1||parts.length()>256)throw new IOException("Некорректные части модели");long sum=0;
            for(int j=0;j<parts.length();j++){
                JSONObject part=parts.getJSONObject(j);String path=part.getString("path");long length=part.getLong("size");
                if(!path.matches("models/"+role+"-[0-9]{3,6}\\.bin")||!paths.add(path)||length<1||length>128L*1024*1024||!part.getString("sha256").matches("[0-9a-f]{64}"))throw new IOException("Некорректный путь части модели");sum+=length;
            }
            if(sum!=size)throw new IOException("Размер частей модели не совпал");total+=size;
        }
        if(total>LIMIT||roles.contains("vision")!=roles.contains("projector"))throw new IOException("Неполная пара встроенного зрения");
        if(roles.contains("vision")&&!data.getString("visionRevision").matches("[0-9a-f]{40}"))throw new IOException("Не закреплена версия встроенного зрения");
    }
}
