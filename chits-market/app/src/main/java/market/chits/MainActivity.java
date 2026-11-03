package market.chits;

import android.app.Activity;
import android.content.ClipData;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.webkit.JavascriptInterface;
import android.webkit.MimeTypeMap;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.zip.ZipFile;

/** Offline-first market. Only the packaged HTTPS origin receives the Java bridge. */
public final class MainActivity extends Activity {
    private static final String ORIGIN = "https://app.chits.local/";
    private static final long APK_LIMIT = 200L * 1024 * 1024;
    private static final long MODEL_LIMIT = 4L * 1024 * 1024 * 1024;
    private WebView web;
    private SharedPreferences prefs;
    private final ExecutorService workers = Executors.newFixedThreadPool(2);
    private final AtomicBoolean modelBusy = new AtomicBoolean(false);
    private final AtomicBoolean inferenceBusy = new AtomicBoolean(false);
    private final java.util.Set<String> downloading = java.util.Collections.synchronizedSet(new java.util.HashSet<String>());
    private volatile int modelProgress;
    private String pendingInstall;
    private ValueCallback<Uri[]> fileCallback;
    private VisionModelManager visionModels;
    private static VisionModelManager sharedVisionModels;
    private final VisionModelManager.Listener modelListener=this::event;
    private android.net.ConnectivityManager.NetworkCallback networkCallback;
    private AssistantPhotos assistantPhotos;
    private final AtomicBoolean photoBusy = new AtomicBoolean(false);

    @Override public void onCreate(Bundle bundle) {
        super.onCreate(bundle);
        getWindow().setStatusBarColor(Color.rgb(16,27,40));
        getWindow().setNavigationBarColor(Color.rgb(16,27,40));
        prefs = getSharedPreferences("market", MODE_PRIVATE);
        assistantPhotos = new AssistantPhotos(this);
        synchronized(MainActivity.class){
            if(sharedVisionModels==null)sharedVisionModels=new VisionModelManager(getApplicationContext(),null);
            visionModels=sharedVisionModels;
        }
        visionModels.setListener(modelListener);
        try {
            seed("orbita-v6", "apks/orbita.apk", "Орбита", "45 офлайн-игр: головоломки, аркады и 3D.", "Игры");
            seed("breach-v2", "apks/breach.apk", "Прорыв", "Автономный шутер с ботами 3 × 3.", "Игры");
        } catch (Exception e) { android.util.Log.e("Chits", "Bundled APK initialization failed", e); }
        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(16,27,40));
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        // Explicit document-picker grants are needed for administrator uploads.
        settings.setAllowContentAccess(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(true);
        web.addJavascriptInterface(new Bridge(), "ChitsNative");
        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageFinished(WebView view, String url) {
                if (url.startsWith(ORIGIN)) visionModels.start(false);
            }
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
                String url = req.getUrl().toString();
                if (!url.startsWith(ORIGIN)) return null;
                String path = req.getUrl().getPath();
                if (path == null || path.equals("/")) path = "/index.html";
                try {
                    if (path.contains("..") || path.contains("\\")) throw new IOException();
                    String ext = MimeTypeMap.getFileExtensionFromUrl(path);
                    String mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext);
                    if (ext.equals("js")) mime = "application/javascript";
                    return new WebResourceResponse(mime == null ? "application/octet-stream" : mime, "UTF-8", getAssets().open("web" + path));
                } catch (IOException e) {
                    return new WebResourceResponse("text/plain", "UTF-8", 404, "Not Found", java.util.Collections.<String,String>emptyMap(), new ByteArrayInputStream(new byte[0]));
                }
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                // No remote document may ever acquire ChitsNative.
                if (req.getUrl().toString().startsWith(ORIGIN)) return false;
                return true;
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                try {
                    Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*");
                    startActivityForResult(intent, 13);
                } catch (Exception e) { fileCallback.onReceiveValue(null); fileCallback = null; }
                return true;
            }
        });
        android.widget.FrameLayout root = new android.widget.FrameLayout(this);
        root.setBackgroundColor(Color.rgb(16,27,40));
        root.addView(web, new android.widget.FrameLayout.LayoutParams(-1,-1));
        // Android 15 enforces edge-to-edge for target 35. Keep controls clear
        // of status/navigation bars on these devices as well as older ones.
        root.setOnApplyWindowInsetsListener((view,insets)->{
            view.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());
            return insets.consumeSystemWindowInsets();
        });
        setContentView(root);
        web.loadUrl(ORIGIN);
        try{
            android.net.ConnectivityManager cm=(android.net.ConnectivityManager)getSystemService(CONNECTIVITY_SERVICE);
            networkCallback=new android.net.ConnectivityManager.NetworkCallback(){
                @Override public void onCapabilitiesChanged(android.net.Network network,android.net.NetworkCapabilities caps){
                    runOnUiThread(()->{if(web!=null)visionModels.start(false);});
                }
            };
            if(cm!=null)cm.registerDefaultNetworkCallback(networkCallback);
        }catch(Exception ignored){ }
    }

    private File apkDir() { File dir = new File(getFilesDir(), "apks"); dir.mkdirs(); return dir; }
    private File modelFile() { return new File(getFilesDir(), "assistant.gguf"); }
    private static JSONObject obj(Object... pairs) {
        JSONObject value = new JSONObject();
        try { for (int i=0; i<pairs.length; i+=2) value.put((String)pairs[i], pairs[i+1]); } catch (Exception ignored) { }
        return value;
    }
    private synchronized JSONArray records() {
        try { return new JSONArray(prefs.getString("downloads", "[]")); } catch (Exception e) { return new JSONArray(); }
    }
    private JSONArray catalog() {
        JSONArray apps=records();
        for(String id:new String[]{"orbita-v6","breach-v2"}) {
            boolean found=false;
            for(int i=0;i<apps.length();i++)if(apps.optJSONObject(i)!=null && id.equals(apps.optJSONObject(i).optString("id")))found=true;
            if(!found){try{JSONObject item=new JSONObject(prefs.getString("bundled-"+id,"{}"));if(item.has("id"))apps.put(item);}catch(Exception ignored){}}
        }
        return apps;
    }
    private synchronized void saveRecord(JSONObject record) throws Exception {
        JSONArray result = new JSONArray();
        JSONArray all = records();
        for (int i=0; i<all.length(); i++) if (!all.getJSONObject(i).optString("id").equals(record.getString("id"))) result.put(all.getJSONObject(i));
        result.put(record);
        prefs.edit().putString("downloads", result.toString()).commit();
    }
    private void event(JSONObject value) {
        runOnUiThread(() -> { if (web != null) web.evaluateJavascript("window.Chits&&window.Chits.onNativeEvent(" + value.toString() + ")", null); });
    }
    private static String message(Exception e) { String m=e.getMessage(); return m == null ? "Операция не выполнена" : m; }
    private static String hex(byte[] bytes) {
        StringBuilder value=new StringBuilder(); for(byte b:bytes) value.append(String.format(Locale.ROOT,"%02x",b & 255)); return value.toString();
    }
    private String digest(File file) throws Exception {
        MessageDigest hash=MessageDigest.getInstance("SHA-256"); byte[] buffer=new byte[65536];
        try(InputStream input=new FileInputStream(file)){int n;while((n=input.read(buffer))!=-1)hash.update(buffer,0,n);}
        return hex(hash.digest());
    }
    private JSONObject apkMetadata(String id, File file, String title, String description, String category) throws Exception {
        try(ZipFile zip=new ZipFile(file)) {
            if(zip.getEntry("AndroidManifest.xml")==null || zip.getEntry("classes.dex")==null) throw new IOException("Это не Android APK");
        }
        PackageInfo info=getPackageManager().getPackageArchiveInfo(file.getAbsolutePath(),0);
        if(info==null || info.packageName==null) throw new IOException("Android не распознал APK");
        if(title.isEmpty()) {
            info.applicationInfo.sourceDir=file.getAbsolutePath(); info.applicationInfo.publicSourceDir=file.getAbsolutePath();
            title=getPackageManager().getApplicationLabel(info.applicationInfo).toString();
        }
        return obj("id",id,"title",title,"description",description,"category",category,"packageName",info.packageName,"versionName",info.versionName==null?"1.0":info.versionName,"size",file.length(),"sha256",digest(file),"fileName",id+".apk","offline",true);
    }
    private void seed(String id,String asset,String title,String description,String category) throws Exception {
        File dest=new File(apkDir(),id+".apk");
        boolean known=false; JSONArray all=records(); for(int i=0;i<all.length();i++) if(all.getJSONObject(i).optString("id").equals(id)) known=true;
        // A deliberate deletion persists; bundled applications remain available for re-copy.
        if(prefs.getBoolean("seeded-"+id,false) && (!dest.exists() || known)) return;
        try(InputStream in=getAssets().open(asset);OutputStream out=new FileOutputStream(dest)){copy(in,out,APK_LIMIT);}
        JSONObject metadata=apkMetadata(id,dest,title,description,category);
        metadata.put("bundled",true).put("downloadUrl","bundled:"+id);
        saveRecord(metadata);
        prefs.edit().putString("bundled-"+id,metadata.toString()).commit();
        prefs.edit().putBoolean("seeded-"+id,true).commit();
    }
    private static long copy(InputStream in,OutputStream out,long limit) throws IOException {
        byte[] buffer=new byte[65536];long total=0;int n;
        while((n=in.read(buffer))!=-1){total+=n;if(total>limit)throw new IOException("Файл слишком большой");out.write(buffer,0,n);}return total;
    }
    private URL checkedURL(String text) throws Exception {
        URL url=new URL(text);
        boolean local=url.getHost().equals("127.0.0.1")||url.getHost().equals("localhost")||url.getHost().equals("10.0.2.2");
        if(!url.getProtocol().equals("https") && !(local && url.getProtocol().equals("http"))) throw new IOException("Для сервера нужен HTTPS-адрес");
        if(url.getUserInfo()!=null)throw new IOException("Логин и пароль нельзя указывать в адресе");
        return url;
    }
    private JSONObject firebaseConfig(){
        try{return new JSONObject(prefs.getString("firebaseConfig","{}"));}catch(Exception e){return new JSONObject();}
    }
    private JSONObject normalizeFirebaseConfig(JSONObject config)throws Exception{
        for(String secret:new String[]{"private_key","private_key_id","client_email","password","serviceAccount"})if(config.has(secret))throw new IOException("Это приватные данные. Нужен только публичный firebaseConfig веб-приложения");
        if(!config.optBoolean("enabled",true))return obj("enabled",false);
        String project=config.optString("projectId"),bucket=config.optString("storageBucket"),key=config.optString("apiKey");
        if(!project.matches("[a-z][a-z0-9-]{4,28}[a-z0-9]")||!bucket.matches("[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]")||!key.matches("[a-zA-Z0-9_-]{15,160}"))throw new IOException("Проверьте projectId, storageBucket и apiKey из Firebase Console");
        JSONObject normalized=obj("enabled",true,"projectId",project,"storageBucket",bucket,"apiKey",key);
        for(String name:new String[]{"authDomain","appId","messagingSenderId","adminEmail"}){
            String value=config.optString(name,"").trim();
            if(value.length()>250)throw new IOException("Поле Firebase слишком длинное");
            normalized.put(name,value);
        }
        return normalized;
    }
    private boolean firebaseApk(URL url,String id){
        JSONObject config=firebaseConfig();
        if(!config.optBoolean("enabled")||!"https".equals(url.getProtocol())||!"firebasestorage.googleapis.com".equals(url.getHost())||(url.getPort()!=-1&&url.getPort()!=443))return false;
        try{
            String expected="/v0/b/"+config.getString("storageBucket")+"/o/"+java.net.URLEncoder.encode("apks/"+id+".apk","UTF-8");
            return expected.equals(url.getPath());
        }catch(Exception e){return false;}
    }
    private HttpURLConnection connection(URL initial) throws Exception {
        URL current=initial;
        for(int i=0;i<8;i++) {
            HttpURLConnection c=(HttpURLConnection)current.openConnection();c.setConnectTimeout(20000);c.setReadTimeout(45000);c.setInstanceFollowRedirects(false);c.setRequestProperty("User-Agent","ChitsMarket/1.0");
            int code=c.getResponseCode();
            if(code==301||code==302||code==303||code==307||code==308){String location=c.getHeaderField("Location");c.disconnect();if(location==null)throw new IOException("Пустое перенаправление");current=checkedURL(new URL(current,location).toString());continue;}
            if(code!=200){c.disconnect();throw new IOException("Сервер вернул HTTP "+code);}return c;
        }
        throw new IOException("Слишком много перенаправлений");
    }
    private String readText(String url) throws Exception {
        HttpURLConnection c=connection(checkedURL(url));try(InputStream in=c.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){copy(in,out,4L*1024*1024);return out.toString("UTF-8");}finally{c.disconnect();}
    }
    private void download(URL url,File target,long expected,String sha,long limit,String type,String id) throws Exception {
        if(expected<=0 || expected>limit || !sha.matches("[0-9a-fA-F]{64}"))throw new IOException("Нет корректного размера или SHA-256 файла");
        HttpURLConnection c=connection(url);MessageDigest hash=MessageDigest.getInstance("SHA-256");long total=0;int previous=-1;
        try(InputStream in=c.getInputStream();OutputStream out=new FileOutputStream(target)) {
            byte[] buffer=new byte[65536];int n;
            while((n=in.read(buffer))!=-1){total+=n;if(total>expected||total>limit)throw new IOException("Размер файла не совпал");hash.update(buffer,0,n);out.write(buffer,0,n);int p=(int)(total*100/expected);if(p!=previous){previous=p;if(type.equals("ai-model"))modelProgress=p;event(obj("type",type,"id",id,"status","downloading","progress",p));}}
        } finally {c.disconnect();}
        if(total!=expected || !hex(hash.digest()).equalsIgnoreCase(sha))throw new IOException("Не совпала контрольная сумма SHA-256");
    }
    private void install(String id) {
        if(!id.matches("[a-zA-Z0-9_-]{1,100}") || !new File(apkDir(),id+".apk").isFile()){event(obj("type","install","id",id,"status","error","error","Сначала сохраните APK на телефон"));return;}
        if(!getPackageManager().canRequestPackageInstalls()) {
            pendingInstall=id;
            event(obj("type","install","id",id,"status","permission","error","Разрешите Chits Market устанавливать приложения и вернитесь назад"));
            startActivityForResult(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,Uri.parse("package:"+getPackageName())),14);return;
        }
        try {
            Uri uri=Uri.parse("content://market.chits.files/apk/"+id);
            Intent intent=new Intent(Intent.ACTION_VIEW).setDataAndType(uri,"application/vnd.android.package-archive").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            intent.setClipData(ClipData.newRawUri("APK",uri));startActivity(intent);
            event(obj("type","install","id",id,"status","opened"));
        } catch(Exception e){event(obj("type","install","id",id,"status","error","error",message(e)));}
    }
    public final class Bridge {
        @JavascriptInterface public String getState() {
            JSONObject ai=visionModels.state();
            try { if(modelBusy.get())ai.put("downloading",true); }catch(Exception ignored){ }
            return obj("serverUrl",prefs.getString("serverUrl",""),"firebaseConfig",firebaseConfig(),"apps",catalog(),"downloads",records(),"ai",ai,"assistantPhoto",assistantPhotos.state()==null?JSONObject.NULL:assistantPhotos.state()).toString();
        }
        @JavascriptInterface public void setFirebaseConfig(String json) {
            try {JSONObject config=normalizeFirebaseConfig(new JSONObject(json));prefs.edit().putString("firebaseConfig",config.toString()).commit();event(obj("type","firebase-config","status","saved"));}
            catch(Exception e){event(obj("type","firebase-config","status","error","error",message(e)));}
        }
        @JavascriptInterface public void pickAssistantPhoto(){
            if(inferenceBusy.get()||photoBusy.get()){event(obj("type","photo","status","error","error","Дождитесь текущей обработки"));return;}
            runOnUiThread(()->{try{startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("image/*"),15);}catch(Exception e){event(obj("type","photo","status","error","error","Не удалось открыть выбор фотографии"));}});
        }
        @JavascriptInterface public void clearAssistantPhoto(){
            if(inferenceBusy.get()||photoBusy.get()){event(obj("type","photo","status","error","error","Дождитесь текущей обработки"));return;}
            assistantPhotos.clear();event(obj("type","photo","status","cleared"));
        }

        @JavascriptInterface public void setServerUrl(String text) {
            try {String value=text.trim();if(!value.isEmpty()){URL url=checkedURL(value);if(url.getQuery()!=null||url.getRef()!=null||!(url.getPath().isEmpty()||url.getPath().equals("/")))throw new IOException("Укажите адрес сервера без пути");value=value.replaceAll("/+$","");}prefs.edit().putString("serverUrl",value).commit();event(obj("type","settings","status","saved"));}
            catch(Exception e){event(obj("type","settings","status","error","error",message(e)));}
        }
        @JavascriptInterface public void downloadApp(String json) {
            workers.execute(()->{
                String id="";File temp=null;boolean locked=false;
                try {
                    JSONObject app=new JSONObject(json);id=app.getString("id");if(!id.matches("[a-zA-Z0-9_-]{1,100}"))throw new IOException("Некорректный ID");
                    if(!downloading.add(id))throw new IOException("Приложение уже скачивается");locked=true;
                    temp=new File(apkDir(),id+".part");
                    if(id.equals("orbita-v6")||id.equals("breach-v2")) {try(InputStream in=getAssets().open(id.equals("orbita-v6")?"apks/orbita.apk":"apks/breach.apk");OutputStream out=new FileOutputStream(temp)){copy(in,out,APK_LIMIT);}}
                    else {
                        String source=app.getString("downloadUrl");
                        URL url=source.startsWith("https://")?checkedURL(source):checkedURL(new URL(checkedURL(prefs.getString("serverUrl","")),source).toString());
                        if(!firebaseApk(url,id)){
                            URL base=checkedURL(prefs.getString("serverUrl",""));
                            if(!url.getProtocol().equals(base.getProtocol())||!url.getHost().equals(base.getHost())||url.getPort()!=base.getPort())throw new IOException("APK должен находиться на настроенном сервере или в вашем Firebase bucket");
                        }
                        download(url,temp,app.getLong("size"),app.getString("sha256"),APK_LIMIT,"download",id);
                    }
                    JSONObject record=apkMetadata(id,temp,app.optString("title"),app.optString("description"),app.optString("category","Приложения"));
                    File dest=new File(apkDir(),id+".apk");if(!temp.renameTo(dest))throw new IOException("Не удалось сохранить APK");saveRecord(record);
                    event(obj("type","download","id",id,"status","done","progress",100,"app",record));
                } catch(Exception e){if(temp!=null)temp.delete();event(obj("type","download","id",id,"status","error","error",message(e)));}
                finally{if(locked)downloading.remove(id);}
            });
        }
        @JavascriptInterface public void installApp(String id) {runOnUiThread(()->install(id));}
        @JavascriptInterface public void deleteDownload(String id) {
            workers.execute(()->{try {if(!id.matches("[a-zA-Z0-9_-]{1,100}"))throw new IOException("Некорректный ID");if(downloading.contains(id))throw new IOException("Дождитесь загрузки");File file=new File(apkDir(),id+".apk");if(file.exists()&&!file.delete())throw new IOException("Не удалось удалить APK");synchronized(MainActivity.this){JSONArray all=records(),next=new JSONArray();for(int i=0;i<all.length();i++)if(!all.getJSONObject(i).optString("id").equals(id))next.put(all.getJSONObject(i));prefs.edit().putString("downloads",next.toString()).commit();}event(obj("type","download","id",id,"status","deleted"));}catch(Exception e){event(obj("type","download","id",id,"status","error","error",message(e)));}});
        }
        @JavascriptInterface public void openApkPicker(){runOnUiThread(()->pick(11));}
        @JavascriptInterface public void importAiModel(){runOnUiThread(()->pick(12));}
        @JavascriptInterface public void downloadAiModel(){visionModels.start(true);}
        @JavascriptInterface public void askAssistant(String prompt){
            if(!inferenceBusy.compareAndSet(false,true)){event(obj("type","assistant","status","error","error","Дождитесь текущего ответа"));return;}
            workers.execute(()->{try{
                if(modelBusy.get()||visionModels.isBusy()||photoBusy.get())throw new IOException("Дождитесь подготовки модели или фотографии");
                if(prompt==null||prompt.trim().isEmpty()||prompt.length()>8000)throw new IOException("Введите сообщение до 8000 символов");
                File image=assistantPhotos.file();
                boolean vision=visionModels.isVisionReady();
                if(image!=null&&!vision)throw new IOException("Для фотографии дождитесь загрузки Qwen-VL и компонента зрения");
                File model=vision?visionModels.modelFile():modelFile();
                if(!model.isFile())throw new IOException("Модель ещё не установлена. Подключитесь к Wi-Fi для автоматической загрузки");
                event(obj("type","assistant","status","generating"));
                String answer;
                synchronized(OfflineAi.class){
                    boolean loaded=vision?OfflineAi.load(model.getAbsolutePath(),visionModels.projectorFile().getAbsolutePath()):OfflineAi.load(model.getAbsolutePath());
                    if(!loaded)throw new IOException("Модель не загрузилась: проверьте GGUF и свободную память");
                    answer=image==null?OfflineAi.generate(prompt,256):OfflineAi.generate(prompt,image.getAbsolutePath(),256);
                }
                if(answer==null||answer.trim().isEmpty())throw new IOException("Модель не вернула текст");
                event(obj("type","assistant","status","done","text",answer));
            }catch(Exception e){event(obj("type","assistant","status","error","error",message(e)));}finally{inferenceBusy.set(false);}});
        }
    }

    private void pick(int request){try{startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*"),request);}catch(Exception e){event(obj("type",request==12?"ai-model":"download","status","error","error","Не найден файловый менеджер"));}}
    private void validateModel(File file)throws Exception{
        try(InputStream in=new FileInputStream(file)){byte[] magic=new byte[4];if(in.read(magic)!=4||magic[0]!='G'||magic[1]!='G'||magic[2]!='U'||magic[3]!='F')throw new IOException("Нужна языковая модель в формате GGUF");}
        if(!OfflineAi.load(file.getAbsolutePath()))throw new IOException("ИИ не распознал модель GGUF");
    }
    @Override protected void onActivityResult(int request,int result,Intent data){
        super.onActivityResult(request,result,data);
        if(request==13){if(fileCallback!=null){fileCallback.onReceiveValue(result==RESULT_OK&&data!=null?new Uri[]{data.getData()}:null);fileCallback=null;}return;}
        if(request==14){String id=pendingInstall;pendingInstall=null;if(id!=null&&getPackageManager().canRequestPackageInstalls())install(id);return;}
        if(request==15){
            if(result!=RESULT_OK||data==null||data.getData()==null)return;
            if(inferenceBusy.get()||!photoBusy.compareAndSet(false,true)){event(obj("type","photo","status","error","error","Дождитесь текущей обработки"));return;}
            final Uri imageUri=data.getData();
            workers.execute(()->{try{
                event(obj("type","photo","status","importing"));
                JSONObject photo=assistantPhotos.importPhoto(imageUri);
                event(obj("type","photo","status","ready","photo",photo,"id",photo.getString("id"),"name",photo.getString("name"),"width",photo.getInt("width"),"height",photo.getInt("height"),"preview",photo.getString("preview")));
            }catch(Exception e){event(obj("type","photo","status","error","error",message(e)));}finally{photoBusy.set(false);}});
            return;
        }
        if((request!=11&&request!=12)||result!=RESULT_OK||data==null||data.getData()==null)return;
        final Uri uri=data.getData();final boolean ai=request==12;
        if(ai&&(visionModels.isBusy()||inferenceBusy.get()||!modelBusy.compareAndSet(false,true)))return;
        workers.execute(()->{File temp=null;try{
            temp=File.createTempFile(ai?"model":"import",".part",getFilesDir());
            event(obj("type",ai?"ai-model":"download","status",ai?"downloading":"importing","progress",0));
            try(InputStream in=getContentResolver().openInputStream(uri);OutputStream out=new FileOutputStream(temp)){if(in==null)throw new IOException("Не удалось открыть файл");copy(in,out,ai?MODEL_LIMIT:APK_LIMIT);}
            if(ai){validateModel(temp);OfflineAi.release();if(!temp.renameTo(modelFile()))throw new IOException("Не удалось сохранить модель");modelBusy.set(false);event(obj("type","ai-model","status","ready","progress",100));}
            else{String id="local-"+digest(temp).substring(0,24);JSONObject record=apkMetadata(id,temp,"","Импортировано с устройства","Приложения");if(!temp.renameTo(new File(apkDir(),id+".apk")))throw new IOException("Не удалось сохранить APK");saveRecord(record);event(obj("type","download","id",id,"status","done","progress",100,"app",record));}
        }catch(Exception e){if(temp!=null)temp.delete();if(ai)modelBusy.set(false);event(obj("type",ai?"ai-model":"download","status","error","error",message(e)));}});
    }
    @Override protected void onResume(){super.onResume();if(visionModels!=null && web!=null)new android.os.Handler().postDelayed(()->{if(web!=null)visionModels.start(false);},1500);}
    @Override public void onBackPressed(){if(web!=null)web.evaluateJavascript("window.Chits&&window.Chits.goBack?window.Chits.goBack():false",value->{if("false".equals(value)||"null".equals(value))finish();});else super.onBackPressed();}
    @Override protected void onDestroy(){
        if(visionModels!=null)visionModels.clearListener(modelListener);
        if(networkCallback!=null){try{((android.net.ConnectivityManager)getSystemService(CONNECTIVITY_SERVICE)).unregisterNetworkCallback(networkCallback);}catch(Exception ignored){ }}
        if(fileCallback!=null)fileCallback.onReceiveValue(null);
        if(web!=null){web.removeJavascriptInterface("ChitsNative");web.destroy();web=null;}
        workers.shutdown();super.onDestroy();
    }
}
