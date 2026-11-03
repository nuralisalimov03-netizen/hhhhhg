package market.chits;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.SystemClock;
import android.util.AtomicFile;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/** A small text-only model. Vision weights are never required for ordinary chat. */
public final class FastModelManager {
    public interface Listener { void onEvent(JSONObject event); }
    private static final String REPO="Qwen/Qwen2.5-0.5B-Instruct-GGUF";
    private static final String LABEL="Qwen2.5 0.5B Instruct · быстрый текст";
    private static final long LIMIT=1024L*1024*1024;
    private static final long MARGIN=64L*1024*1024;
    private final Context context;
    private final SharedPreferences prefs;
    private final ExecutorService worker=Executors.newSingleThreadExecutor();
    private final AtomicBoolean busy=new AtomicBoolean(false),retryScheduled=new AtomicBoolean(false);
    private final android.os.Handler handler=new android.os.Handler(android.os.Looper.getMainLooper());
    private final Runnable retry=()->{retryScheduled.set(false);start(false);};
    private final File directory;
    private final AtomicFile manifestFile;
    private volatile Listener listener;
    private volatile String status="waiting-wifi",error="",phase="",component="";
    private volatile long downloadedBytes,totalBytes,lastAttempt,lastNetworkCheck,lastEvent;
    private volatile int progress,lastPercent=-1;
    private volatile boolean restoreAttempted;

    public FastModelManager(Context context,Listener listener){
        this.context=context.getApplicationContext();this.listener=listener;
        prefs=this.context.getSharedPreferences("fast-model",Context.MODE_PRIVATE);
        directory=new File(this.context.getFilesDir(),"fast-downloads");directory.mkdirs();
        manifestFile=new AtomicFile(new File(directory,"manifest.json"));
        if(modelFile().isFile()){status="ready";totalBytes=modelFile().length();downloadedBytes=totalBytes;progress=100;}
    }
    public File modelFile(){return new File(context.getFilesDir(),"assistant.gguf");}
    public boolean isBusy(){return busy.get();}
    public synchronized void setListener(Listener listener){this.listener=listener;}
    public synchronized void clearListener(Listener expected){if(listener==expected)listener=null;}
    public JSONObject state(){
        boolean available=OfflineAi.isSupported()&&modelFile().isFile();
        boolean embedded=BundledModels.hasRole(context,"text");
        boolean knownDownload="download".equals(prefs.getString("source",""))
            &&modelFile().length()==prefs.getLong("bytes",-1)&&modelFile().lastModified()==prefs.getLong("mtime",-1);
        boolean installedBundle=BundledModels.isInstalledFromBundle(context,"text");
        String label=knownDownload?LABEL:installedBundle?"Встроенная текстовая GGUF-модель":available?"Локальная текстовая GGUF-модель":LABEL;
        String reason=!OfflineAi.isSupported()?"Для локального ИИ нужен Android ARM64":!error.isEmpty()?error:
            available?"Быстрый текстовый режим не загружает модуль зрения":embedded?"Текстовая модель включена в APK и будет подготовлена без интернета":"В этом APK нет весов: небольшая текстовая модель загрузится по Wi-Fi. Фото используют отдельный профиль";
        return object("mode","text","available",available,"installed",available,"vision",false,
            "downloading",busy.get(),"embedded",embedded,"embeddedVision",BundledModels.hasRole(context,"vision"),
            "status",status,"phase",phase,"component",component,"progress",progress,
            "downloadedBytes",downloadedBytes,"totalBytes",totalBytes,"model",label,"targetModel",LABEL,
            "reason",reason,"error",error,"autoDownload",!embedded);
    }
    /** Embedded assets are restored before asking for a network connection. */
    public void start(boolean allowMetered){
        if(!OfflineAi.isSupported()){status="error";error="Для ИИ нужен Android ARM64";emit();return;}
        if(busy.get())return;
        if(allowMetered&&"error".equals(status)&&(BundledModels.hasRole(context,"text")||BundledModels.hasRole(context,"vision")))restoreAttempted=false;
        if(restoreAttempted&&modelFile().isFile()){status="ready";return;}
        long now=SystemClock.elapsedRealtime();
        if(!allowMetered&&lastAttempt!=0&&now-lastAttempt<60000){schedule(60000-(now-lastAttempt));return;}
        if(!busy.compareAndSet(false,true))return;
        lastAttempt=now;error="";
        worker.execute(()->prepare(allowMetered));
    }
    private void prepare(boolean allowMetered){
        File part=new File(directory,"text.part");
        try{
            if(!restoreAttempted){
                restoreAttempted=true;
                phase="restoring";component="Подготовка встроенной текстовой модели";status="downloading";emit();
                BundledModels.restoreText(context,(role,current,total)->{
                    downloadedBytes=current;totalBytes=total;progress=(int)(current*100/total);
                    component=role.equals("text")?"Распаковка текстовой модели":role.equals("vision")?"Распаковка визуальной модели":"Распаковка модуля зрения";
                    throttle();
                });
            }
            if(modelFile().isFile()){status="ready";phase="";progress=100;downloadedBytes=modelFile().length();totalBytes=downloadedBytes;return;}
            if(!networkAllowed(allowMetered))throw new WaitingForWifi();
            phase="downloading";status="downloading";component="Проверка версии текстовой модели";emit();
            JSONObject manifest=manifest();long expected=manifest.getLong("size");
            totalBytes=expected;downloadedBytes=part.isFile()?Math.min(expected,part.length()):0;
            if(context.getFilesDir().getUsableSpace()<expected-downloadedBytes+MARGIN)throw new IOException("Для быстрой модели нужно ещё около "+((expected-downloadedBytes+MARGIN+1048575)/1048576)+" МБ");
            component="Qwen 0.5B · быстрый текст";lastNetworkCheck=0;
            URL source=new URI("https","huggingface.co","/"+REPO+"/resolve/"+manifest.getString("revision")+"/"+manifest.getString("path"),null).toURL();
            ModelDownloader.download(source,part,expected,manifest.getString("sha256"),LIMIT,(current,total)->{
                long now=SystemClock.elapsedRealtime();
                if(now-lastNetworkCheck>1500){lastNetworkCheck=now;if(!networkAllowed(allowMetered)&&current<total)throw new WaitingForWifi();}
                downloadedBytes=current;totalBytes=total;progress=(int)(current*100/total);throttle();
            });
            phase="validating";component="Проверка текстовой модели";emit();
            synchronized(OfflineAi.class){try{if(!OfflineAi.load(part.getAbsolutePath()))throw new IOException("Текстовая модель не загрузилась");}finally{OfflineAi.release();}}
            if(modelFile().isFile())throw new IOException("На устройстве уже сохранена другая текстовая модель");
            if(!part.renameTo(modelFile()))throw new IOException("Не удалось сохранить текстовую модель");
            if(!prefs.edit().putString("source","download").putString("revision",manifest.getString("revision"))
                .putString("sha256",manifest.getString("sha256")).putLong("bytes",expected).putLong("mtime",modelFile().lastModified()).commit())throw new IOException("Не удалось сохранить сведения о модели");
            status="ready";phase="";component="";progress=100;
        }catch(WaitingForWifi waiting){
            status="waiting-wifi";phase="";error="";lastAttempt=0;handler.removeCallbacks(retry);retryScheduled.set(false);
        }catch(Exception failure){
            status="error";phase="";error=failure.getMessage()==null?"Не удалось подготовить текстовую модель":failure.getMessage();
            if(!allowMetered&&!networkAllowed(false)&&!BundledModels.hasRole(context,"text")){status="waiting-wifi";error="";lastAttempt=0;}
        }finally{
            busy.set(false);emit();
            if("waiting-wifi".equals(status)&&networkAllowed(false))schedule(300);
        }
    }
    private boolean networkAllowed(boolean allowMetered){
        try{
            ConnectivityManager cm=(ConnectivityManager)context.getSystemService(Context.CONNECTIVITY_SERVICE);if(cm==null)return false;
            Network network=cm.getActiveNetwork();NetworkCapabilities capabilities=network==null?null:cm.getNetworkCapabilities(network);
            if(capabilities==null||!capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)||!capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED))return false;
            return allowMetered||(!cm.isActiveNetworkMetered()&&(capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)||capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)));
        }catch(SecurityException denied){return false;}
    }
    private void schedule(long delay){if(retryScheduled.compareAndSet(false,true))handler.postDelayed(retry,Math.max(250,delay));}
    private void throttle(){long now=SystemClock.elapsedRealtime();if(progress!=lastPercent||now-lastEvent>350){lastEvent=now;lastPercent=progress;emit();}}
    private void emit(){Listener current=listener;if(current!=null)current.onEvent(object("type","ai-model","mode","text","status",status,"phase",phase,"component",component,"progress",progress,"downloadedBytes",downloadedBytes,"totalBytes",totalBytes,"error",error));}
    private JSONObject manifest()throws Exception{
        if(manifestFile.getBaseFile().isFile()){
            try{byte[] bytes=manifestFile.readFully();if(bytes.length>1024*1024)throw new IOException("Слишком большой манифест");JSONObject cached=new JSONObject(new String(bytes,"UTF-8"));validate(cached);return cached;}
            catch(Exception corrupt){manifestFile.delete();new File(directory,"text.part").delete();}
        }
        String api="https://huggingface.co/api/models/"+REPO;
        String revision=new JSONObject(readText(api+"/revision/main")).getString("sha");
        if(!revision.matches("[0-9a-f]{40}"))throw new IOException("Не удалось проверить версию Qwen");
        JSONArray files=new JSONArray(readText(api+"/tree/"+revision+"?recursive=false&limit=1000"));JSONObject selected=null;
        for(int i=0;i<files.length();i++){
            JSONObject file=files.getJSONObject(i);String name=file.optString("path").toLowerCase(Locale.ROOT);
            if(name.endsWith(".gguf")&&name.contains("q4_k_m")&&!name.startsWith("mmproj")&&!name.contains("/")&&!name.contains("..")){
                if(selected!=null)throw new IOException("Неоднозначный файл быстрой Qwen");selected=file;
            }
        }
        if(selected==null)throw new IOException("Официальная Qwen 0.5B Q4_K_M не найдена");
        JSONObject result=object("schema",1,"repo",REPO,"revision",revision,"path",selected.getString("path"),"size",selected.getLong("size"),"sha256",selected.getJSONObject("lfs").getString("oid"));validate(result);
        FileOutputStream output=null;try{output=manifestFile.startWrite();output.write(result.toString().getBytes("UTF-8"));manifestFile.finishWrite(output);}catch(Exception e){if(output!=null)manifestFile.failWrite(output);throw e;}return result;
    }
    private static void validate(JSONObject data)throws Exception{
        String name=data.getString("path"),lower=name.toLowerCase(Locale.ROOT);long size=data.getLong("size");
        if(data.getInt("schema")!=1||!REPO.equals(data.getString("repo"))||!data.getString("revision").matches("[0-9a-f]{40}")
            ||name.contains("/")||name.contains("\\")||name.contains("..")||!lower.endsWith(".gguf")||!lower.contains("q4_k_m")||lower.startsWith("mmproj")
            ||size<4||size>LIMIT||!data.getString("sha256").matches("[0-9a-fA-F]{64}"))throw new IOException("Неверный манифест быстрой модели");
    }
    private static String readText(String address)throws Exception{
        URL url=new URL(address);
        for(int redirects=0;redirects<8;redirects++){
            if(!"https".equals(url.getProtocol())||url.getUserInfo()!=null)throw new IOException("Для Qwen нужен HTTPS");
            HttpURLConnection c=(HttpURLConnection)url.openConnection();c.setConnectTimeout(20000);c.setReadTimeout(45000);c.setInstanceFollowRedirects(false);c.setRequestProperty("Accept","application/json");c.setRequestProperty("User-Agent","ChitsMarket/1.2");
            int code=c.getResponseCode();if(code==301||code==302||code==303||code==307||code==308){String location=c.getHeaderField("Location");c.disconnect();if(location==null)throw new IOException("Пустое перенаправление Qwen");url=new URL(url,location);continue;}
            if(code!=200){c.disconnect();throw new IOException("Сервер Qwen вернул HTTP "+code);}
            try(InputStream in=c.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){
                byte[] b=new byte[8192];int n;while((n=in.read(b))!=-1){if(out.size()+n>4*1024*1024)throw new IOException("Слишком большой ответ Qwen");out.write(b,0,n);}return out.toString("UTF-8");
            }finally{c.disconnect();}
        }
        throw new IOException("Слишком много перенаправлений Qwen");
    }
    private static JSONObject object(Object... pairs){JSONObject data=new JSONObject();try{for(int i=0;i<pairs.length;i+=2)data.put((String)pairs[i],pairs[i+1]);}catch(Exception ignored){ }return data;}
    private static final class WaitingForWifi extends IOException{private static final long serialVersionUID=1L;}
}
