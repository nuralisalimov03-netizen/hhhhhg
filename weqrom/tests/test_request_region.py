"""Execute the real request-map/URL helpers, not TikTok or a simulated hook.

Production sources compile against the genuine Android SDK. Only Context and
in-memory SharedPreferences are doubled for the JVM executions below.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


TOOLS = Path(os.environ.get('CHITS_TOOLS_DIR', '/workspace/.chits-tools'))
ECJ = TOOLS / 'ecj.jar'
ANDROID = TOOLS / 'platform/android-35/android.jar'
SOURCE = Path(__file__).resolve().parents[1] / 'app/src/main/java/app/weqrom'

CONTEXT = '''package android.content;
public abstract class Context {
 public abstract Context getApplicationContext();
 public abstract SharedPreferences getSharedPreferences(String name,int mode);
}
'''

HARNESS = '''import app.weqrom.RequestRegion;
import app.weqrom.RegionCatalog;
import android.content.Context;
import android.content.SharedPreferences;
import java.lang.reflect.*;
import java.util.*;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;

public class RequestRegionHarness {
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
 static final class Memory implements InvocationHandler {
  final Map<String,Object> values=new HashMap<String,Object>();
  final List<String> readKeys=new ArrayList<String>();
  int reads,writes;boolean failEdits;
  final SharedPreferences preferences=(SharedPreferences)Proxy.newProxyInstance(
   RequestRegionHarness.class.getClassLoader(),new Class<?>[]{SharedPreferences.class},this);
  public Object invoke(Object proxy,Method method,Object[] args){
   if(method.getName().equals("getString")){reads++;readKeys.add((String)args[0]);return values.containsKey(args[0])?(String)values.get(args[0]):args[1];}
   if(method.getName().equals("edit")){
    final Map<String,Object> pending=new HashMap<String,Object>();
    return Proxy.newProxyInstance(RequestRegionHarness.class.getClassLoader(),new Class<?>[]{SharedPreferences.Editor.class},new InvocationHandler(){
     public Object invoke(Object proxy,Method operation,Object[] parameters){
      if(operation.getName().equals("putString")){pending.put((String)parameters[0],parameters[1]);return proxy;}
      if(operation.getName().equals("apply")){
       if(failEdits)throw new IllegalStateException("private-edit-message-token");
       values.putAll(pending);writes++;return null;
      }
      throw new AssertionError("Unexpected preference editor operation: "+operation.getName());
     }
    });
   }
   throw new AssertionError("Unexpected preference operation: "+method.getName());
  }
 }
 static final class BootContext extends Context {
  Context application;int reads;boolean broken,brokenApplication;
  final Map<String,Memory> stores=new HashMap<String,Memory>();
  Memory store(String file){if(!stores.containsKey(file))stores.put(file,new Memory());return stores.get(file);}
  public Context getApplicationContext(){if(brokenApplication)throw new IllegalStateException("private-application-message-token");return application;}
  public SharedPreferences getSharedPreferences(String file,int mode){
   reads++;check(mode==0,"Non-private storage requested");
   check(file.equals("weqrom")||file.equals("ttmod_settings")||file.equals("ttnet_store_region"),"Unexpected preference file");
   if(broken)throw new IllegalStateException("private-storage-message-token");
   return store(file).preferences;
  }
 }
 static Map<String,Object> input(){
  Map<String,Object> map=new LinkedHashMap<String,Object>();
  map.put("region","RU");map.put("carrier_region","RU");map.put("op_region","RU");
  map.put("sys_region","RU");map.put("carrier_region_v2","250");map.put("mcc_mnc","25001");
  map.put("account_region","RU");map.put("current_region","RU");map.put("residence","RU");
  map.put("store_region","RU");map.put("x-tt-store-region","RU");map.put("Cookie","store-country-code=ru");
  map.put("app_language","ru");
  map.put("token","private-auth-token");return map;
 }
 static void regions(Map map,String country){
  for(String key:new String[]{"region","carrier_region","op_region","sys_region"})check(country.equals(map.get(key)),"Inconsistent region field: "+key);
  RegionCatalog.Entry entry=RegionCatalog.find(country);
  if(entry!=null){
   check(entry.mccMnc.substring(0,3).equals(map.get("carrier_region_v2")),"MCC is not numeric country prefix");
   check(entry.mccMnc.equals(map.get("mcc_mnc")),"MCC/MNC does not match same country snapshot");
  }
 }
 static String clientUrl(String country){
  RegionCatalog.Entry entry=RegionCatalog.find(country);
  return "https://example.invalid/feed?region="+country+"&carrier_region="+country+"&op_region="+country+
   "&sys_region="+country+"&carrier_region_v2="+entry.mccMnc.substring(0,3)+"&mcc_mnc="+entry.mccMnc;
 }
 public static void main(String[] args)throws Exception {
  String scenario=args[0];
  if(scenario.equals("copy")){
   Map<String,Object> original=input();Map<String,Object> before=new LinkedHashMap<String,Object>(original);
   HashMap copy=RequestRegion.rewrite(Collections.unmodifiableMap(original));
   check(copy!=original,"Caller map reused");regions(copy,"BY");check(original.equals(before),"Caller map mutated");
   for(String key:new String[]{"account_region","current_region","residence","store_region","x-tt-store-region","Cookie","app_language","token"})check(original.get(key).equals(copy.get(key)),"Non-client-region field changed: "+key);
   copy.put("new","value");check(!original.containsKey("new"),"Copy still aliases original map");
   String summary=RequestRegion.summary(null);
   check(summary.contains("BY")&&summary.contains("sys_region")&&summary.contains("carrier_region_v2")&&summary.contains("mcc_mnc"),"Local rewrite omitted from report");
   check(!summary.contains("private-auth-token")&&!summary.contains("app_language"),"Request contents leaked into report");
  }else if(scenario.equals("selection")){
   RequestRegion.select(" jp ");check("JP".equals(RequestRegion.country()),"ISO was not normalized");
   regions(RequestRegion.rewrite(input()),"JP");
   for(String invalid:new String[]{"RU","XX","GB1","",null}){
    try{RequestRegion.select(invalid);throw new AssertionError("Unsupported country accepted");}catch(IllegalArgumentException expected){}
    check("JP".equals(RequestRegion.country()),"Invalid country changed selection");
   }
   RequestRegion.select("US");
   String report=RequestRegion.summary(null);
   check(report.contains("Регион параметров клиента: US")&&report.contains("Последняя локальная замена: JP"),"Selected and last prepared country conflated");
   regions(RequestRegion.rewrite(input()),"US");
  }else if(scenario.equals("missing")){
   Map<String,Object> map=new HashMap<String,Object>();map.put("account_region","RU");map.put("current_region","RU");
   HashMap copy=RequestRegion.rewrite(map);check(copy.equals(map)&&copy.size()==2,"Absent regional fields were inserted");
   check(RequestRegion.summary(null).contains("Карт с заменой региона: 0"),"No-region map claimed a rewrite");
   map.put("carrier_region",null);RequestRegion.select("DE");copy=RequestRegion.rewrite(map);
   check("DE".equals(copy.get("carrier_region"))&&!copy.containsKey("region")&&!copy.containsKey("op_region"),"Partial field scope changed");
   check(map.get("carrier_region")==null,"Partial input was mutated");
   check(RequestRegion.rewrite(null)==null,"Null map no longer propagates");
   check(RequestRegion.summary(null).contains("Подготовлено карт параметров: 2"),"Null input was counted as prepared");
  }else if(scenario.equals("all_countries")){
   for(RegionCatalog.Entry entry:RegionCatalog.all()){
    RequestRegion.select(entry.iso);regions(RequestRegion.rewrite(input()),entry.iso);
    StringBuilder url=new StringBuilder(clientUrl("BY"));RequestRegion.rewriteUrl(url);
    check(clientUrl(entry.iso).equals(url.toString()),"Country codes drifted between map and URL: "+entry.iso);
   }
  }else if(scenario.equals("url")){
   String before="https://example.invalid/feed?keep=a%2Bb+z%20x&region=RU&%72egion=KZ&carrier_region=RU&op_region=RU&sys_region=RU&carrier_region_v2=250&mcc_mnc=25001&region&empty=&flag&&REGION=RU&account_region=RU&current_region=RU&residence=RU&store_region=RU&Cookie=store-country-code%3Dru&nested=https%3A%2F%2Fnested.invalid%2F%3Fregion%3DRU%26signature%3Dkeep#frag?region=RU";
   String after="https://example.invalid/feed?keep=a%2Bb+z%20x&region=BY&%72egion=BY&carrier_region=BY&op_region=BY&sys_region=BY&carrier_region_v2=257&mcc_mnc=25701&region=BY&empty=&flag&&REGION=RU&account_region=RU&current_region=RU&residence=RU&store_region=RU&Cookie=store-country-code%3Dru&nested=https%3A%2F%2Fnested.invalid%2F%3Fregion%3DRU%26signature%3Dkeep#frag?region=RU";
   StringBuilder url=new StringBuilder(before);RequestRegion.rewriteUrl(url);check(after.equals(url.toString()),"URL query bytes or protected values changed: "+url);
   RequestRegion.select("JP");StringBuilder dynamic=new StringBuilder(clientUrl("BY"));RequestRegion.rewriteUrl(dynamic);
   check(clientUrl("JP").equals(dynamic.toString()),"URL selection was not dynamic");
   String report=RequestRegion.summary(null);
   check(report.contains("URL с заменой региона: 2")&&report.contains("Последняя замена в URL: JP")&&report.contains("mcc_mnc"),"URL observation missing");
   for(String secret:new String[]{"example.invalid","nested.invalid","keep=","store-country-code","RU"})check(!report.contains(secret),"URL contents leaked into diagnostics: "+secret);
  }else if(scenario.equals("url_signed")){
   for(String signingKey:new String[]{"X-Bogus","x-gnarly","%5Fsignature","X-Amz-Signature","x-goog-signature","%53iGnAtUrE"}){
    String signed="https://example.invalid/feed?region=RU&sys_region=RU&"+signingKey+"=private-signature&region=KZ#keep";
    StringBuilder url=new StringBuilder(signed);RequestRegion.rewriteUrl(url);
    check(signed.equals(url.toString()),"Already signed query was rewritten: "+signingKey);
   }
   String report=RequestRegion.summary(null);
   check(report.contains("Подписанных URL сохранено: 6")&&report.contains("URL с заменой региона: 0"),"Signed skip observation incorrect");
   check(!report.contains("private-signature")&&!report.contains("example.invalid"),"Signed URL leaked into diagnostics");
  }else if(scenario.equals("url_boundaries")){
   RequestRegion.rewriteUrl(null);
   for(String unchanged:new String[]{"","https://example.invalid/region=RU","https://example.invalid/#?region=RU","https://example.invalid/?account_region=RU&current_region=RU&residence=RU&store_region=RU","https://example.invalid/?REGION=RU&region%ZZ=RU&%2572egion=RU&region%00=RU","https://example.invalid/?nested=region%3DRU%26sys_region%3DRU#?region=RU"}){
    StringBuilder url=new StringBuilder(unchanged);RequestRegion.rewriteUrl(url);
    check(unchanged.equals(url.toString()),"URL scope or malformed-key preservation changed: "+unchanged);
   }
   StringBuilder malformed=new StringBuilder("https://example.invalid/?bad%=raw%ZZ&X-Bogus%=plain&region=RU#tail%ZZ");
   RequestRegion.rewriteUrl(malformed);
   check("https://example.invalid/?bad%=raw%ZZ&X-Bogus%=plain&region=BY#tail%ZZ".equals(malformed.toString()),"Malformed percent escape corrupted valid fields or unrelated bytes");
   check(RequestRegion.summary(null).contains("Подготовлено URL: 7"),"Null URL was counted or boundary URL omitted");
  }else if(scenario.equals("early")){
   RequestRegion.initialize(null);BootContext base=new BootContext();
   base.store("weqrom").values.put("region","de");base.store("ttmod_settings").values.put("selected_region_iso","jp");
   check(base.getApplicationContext()==null,"Fixture did not exercise early context");
   RequestRegion.initialize(base);check("DE".equals(RequestRegion.country()),"Own preference was not preferred");
   int reads=base.reads;check(reads==1,"Source preference needlessly read after valid own ISO");
   base.store("weqrom").values.put("region","FR");RequestRegion.initialize(base);
   check(base.reads==reads&&"DE".equals(RequestRegion.country()),"Initialization repeatedly read/reset preferences");
   int stringReads=base.store("weqrom").reads;
   for(int i=0;i<1000;i++){
    regions(RequestRegion.rewrite(input()),"DE");StringBuilder url=new StringBuilder(clientUrl("BY"));RequestRegion.rewriteUrl(url);
    check(clientUrl("DE").equals(url.toString()),"Early URL used a different cached preference");
   }
   check(base.reads==reads,"Request rewrite read disk/preferences");
   check(base.store("weqrom").reads==stringReads,"Request rewrite read the retained preference handle");
  }else if(scenario.equals("bound")){
   BootContext base=new BootContext(),application=new BootContext();application.application=application;base.application=application;
   base.store("weqrom").values.put("region","DE");application.store("weqrom").values.put("region","jp");
   RequestRegion.initialize(base);
   check("JP".equals(RequestRegion.country())&&base.reads==0&&application.reads==1,"Bound Application context was not preferred");
  }else if(scenario.equals("fallback_mod")){
   BootContext base=new BootContext();base.store("weqrom").values.put("region","RU");
   base.store("ttmod_settings").values.put("selected_region_iso"," gb ");RequestRegion.initialize(base);
   check("GB".equals(RequestRegion.country())&&base.reads==2,"Valid source-mod preference did not replace unsupported own ISO");
  }else if(scenario.equals("fallback_default")){
   BootContext base=new BootContext();base.store("weqrom").values.put("region",Integer.valueOf(42));
   base.store("ttmod_settings").values.put("selected_region_iso","XX");RequestRegion.initialize(base);
   check("BY".equals(RequestRegion.country()),"Malformed/unsupported preferences did not keep Belarus");
  }else if(scenario.equals("storage")){
   RequestRegion.select("JP");BootContext base=new BootContext();base.broken=true;RequestRegion.initialize(base);
   check("JP".equals(RequestRegion.country()),"Storage failure overwrote valid choice");
   String report=RequestRegion.summary(base);check(report.contains("не удалось синхронизировать"),"Storage failure was not reported");
   check(!report.contains("private-storage-message-token"),"Storage exception contents leaked");
   base.broken=false;base.store("weqrom").values.put("region","GB");RequestRegion.initialize(base);
   check("GB".equals(RequestRegion.country())&&!RequestRegion.summary(base).contains("не удалось синхронизировать"),"Failed initialization could not retry after storage recovery");
  }else if(scenario.equals("advanced")){
   BootContext base=new BootContext();Memory own=base.store("weqrom");own.values.put("region","JP");
   RequestRegion.initialize(base);check("JP".equals(RequestRegion.country()),"Fresh process did not load stored own choice");
   RequestRegion.onModRegionSelected(13);
   check("BY".equals(RequestRegion.country())&&"BY".equals(own.values.get("region")),"Source Belarus array index was treated as a legacy id");
   regions(RequestRegion.rewrite(input()),"BY");check(own.writes==1,"Belarus source selection not persisted exactly once");
   int us=RegionCatalog.find("US").index;RequestRegion.onModRegionSelected(us);
   check("US".equals(RequestRegion.country())&&"US".equals(own.values.get("region")),"Original selector did not synchronize own UI/request country");
   regions(RequestRegion.rewrite(input()),"US");int writes=own.writes;
   RequestRegion.onModRegionSelected(us);RequestRegion.onModRegionSelected(-1);RequestRegion.onModRegionSelected(191);RequestRegion.onModRegionSelected(Integer.MAX_VALUE);
   check(own.writes==writes&&"US".equals(RequestRegion.country()),"Repeated or invalid source indices changed/persisted a country");
   if(args.length>1){
    Properties saved=new Properties();saved.setProperty("region",(String)own.values.get("region"));
    java.io.OutputStream output=new java.io.FileOutputStream(args[1]);try{saved.store(output,"Selection preference fixture");}finally{output.close();}
   }
  }else if(scenario.equals("reload_advanced")){
   check("BY".equals(RequestRegion.country()),"Reload process inherited a live country cache");
   Properties saved=new Properties();java.io.InputStream input=new java.io.FileInputStream(args[1]);try{saved.load(input);}finally{input.close();}
   BootContext base=new BootContext();base.store("weqrom").values.put("region",saved.getProperty("region"));
   RequestRegion.initialize(base);
   check("US".equals(RequestRegion.country()),"Original selector choice was lost in a fresh process");
   regions(RequestRegion.rewrite(input()),"US");
  }else if(scenario.equals("persistence")){
   BootContext base=new BootContext();Memory own=base.store("weqrom");own.values.put("region","jp");RequestRegion.initialize(base);
   check(own.writes==0,"Initialization unexpectedly wrote selection state");
   RequestRegion.select("JP");check(own.writes==1&&"JP".equals(own.values.get("region")),"Selection did not persist canonical uppercase ISO");
   RequestRegion.select(" jp ");check(own.writes==1,"Same canonical selection caused a duplicate preference write");
   own.values.put("region","BY");RequestRegion.select("JP");
   check(own.writes==2&&"JP".equals(own.values.get("region")),"Same cached country did not repair an inconsistent stored choice");
   own.failEdits=true;RequestRegion.select("DE");
   check("DE".equals(RequestRegion.country())&&"JP".equals(own.values.get("region")),"Failed persistence lost the valid live country");
   String report=RequestRegion.summary(base);check(report.contains("не удалось синхронизировать")&&!report.contains("private-edit-message-token"),"Selection storage failure missing or leaked details");
   regions(RequestRegion.rewrite(input()),"DE");
   own.failEdits=false;RequestRegion.select("DE");
   check(own.writes==3&&"DE".equals(own.values.get("region"))&&!RequestRegion.summary(base).contains("не удалось синхронизировать"),"Selection persistence could not recover");
  }else if(scenario.equals("store_report")){
   BootContext base=new BootContext();Memory own=base.store("weqrom"),mod=base.store("ttmod_settings"),store=base.store("ttnet_store_region");
   own.values.put("region","DE");mod.values.put("selected_region_iso","jp");store.values.put("store_region","rU");
   store.values.put("store_sec_uid","private-sec-uid-token");store.values.put("store_region_src","private-source-token");store.values.put("Cookie","private-cookie-token");
   Map<String,Object> ownBefore=new HashMap<String,Object>(own.values),modBefore=new HashMap<String,Object>(mod.values),storeBefore=new HashMap<String,Object>(store.values);
   check("BY".equals(RequestRegion.country()),"Report fixture did not start with cached Belarus");
   String report=RequestRegion.summary(base);
   check(report.contains("Регион параметров клиента: BY")&&report.contains("Последний сохранённый store_region: RU"),"Stored server-routing country was conflated with client choice");
   check(report.contains("локальное сохранённое значение")&&report.contains("не проверка текущего региона на сервере"),"Persisted value was presented as live network proof");
   check("BY".equals(RequestRegion.country())&&own.values.equals(ownBefore)&&mod.values.equals(modBefore)&&store.values.equals(storeBefore),"Reporting changed cached selection or preferences");
   check(base.reads==1&&own.reads==0&&mod.reads==0&&store.readKeys.equals(Arrays.asList("store_region")),"Report read unrelated preference keys or selection stores");
   check(own.writes==0&&mod.writes==0&&store.writes==0,"Report wrote preferences");
   for(String secret:new String[]{"private-sec-uid-token","private-source-token","private-cookie-token","store_sec_uid","store_region_src"})check(!report.contains(secret),"Routing report exposed unrelated state: "+secret);
   BootContext bound=new BootContext(),application=new BootContext();bound.application=application;application.application=application;
   bound.store("ttnet_store_region").values.put("store_region","RU");application.store("ttnet_store_region").values.put("store_region","by");
   check(RequestRegion.summary(bound).contains("Последний сохранённый store_region: BY")&&bound.reads==0&&application.reads==1,"Routing report did not prefer bound Application context");
  }else if(scenario.equals("store_report_unknown")){
   check(RequestRegion.summary(null).contains("Последний сохранённый store_region: неизвестно"),"Null report context invented a stored country");
   BootContext base=new BootContext();Memory store=base.store("ttnet_store_region");
   for(Object invalid:new Object[]{null,"","R1"," R","ru ","РФ","RU-private-long-country-token",Integer.valueOf(42)}){
    store.values.put("store_region",invalid);String report=RequestRegion.summary(base);
    check(report.contains("Последний сохранённый store_region: неизвестно"),"Malformed stored country was accepted: "+invalid);
    check(!report.contains("private-long-country-token"),"Malformed country leaked into report");
   }
   base.broken=true;String storageFailure=RequestRegion.summary(base);
   check(storageFailure.contains("Последний сохранённый store_region: неизвестно")&&!storageFailure.contains("private-storage-message-token"),"Routing storage error leaked or invented a country");
   base.broken=false;base.brokenApplication=true;String appFailure=RequestRegion.summary(base);
   check(appFailure.contains("Последний сохранённый store_region: неизвестно")&&!appFailure.contains("private-application-message-token"),"Application context error leaked or invented a country");
   check("BY".equals(RequestRegion.country())&&store.writes==0,"Unknown routing diagnostic changed selection or preferences");
  }else if(scenario.equals("concurrency")){
   final CountDownLatch begin=new CountDownLatch(1);final AtomicReference<Throwable> failure=new AtomicReference<Throwable>();
   final Map<String,Object> original=Collections.unmodifiableMap(input());List<Thread> threads=new ArrayList<Thread>();
   threads.add(new Thread(new Runnable(){public void run(){try{begin.await();for(int i=0;i<20000;i++)RequestRegion.select((i&1)==0?"JP":"GB");}catch(Throwable error){failure.compareAndSet(null,error);}}}));
   for(int reader=0;reader<4;reader++)threads.add(new Thread(new Runnable(){public void run(){try{
    begin.await();for(int i=0;i<3000;i++){Map result=RequestRegion.rewrite(original);String iso=(String)result.get("region");
     check(iso.equals("BY")||iso.equals("JP")||iso.equals("GB"),"Unexpected concurrent country");regions(result,iso);
     check("RU".equals(result.get("account_region")),"Concurrent rewrite changed account country");
     StringBuilder url=new StringBuilder(clientUrl("BY"));RequestRegion.rewriteUrl(url);String text=url.toString();
     int start=text.indexOf("?region=")+8;String urlCountry=text.substring(start,start+2);
     check(text.equals(clientUrl(urlCountry)),"URL country and MCC used different concurrent snapshots");}
   }catch(Throwable error){failure.compareAndSet(null,error);}}}));
   for(Thread thread:threads)thread.start();begin.countDown();for(Thread thread:threads)thread.join();
   if(failure.get()!=null)throw new AssertionError("Concurrent selection/rewrite failure",failure.get());
   check(RequestRegion.summary(null).contains("Карт с заменой региона: 12000"),"Concurrent local counter lost prepared maps");
   check(RequestRegion.summary(null).contains("URL с заменой региона: 12000"),"Concurrent URL counter lost prepared URLs");
   regions(original,"RU");
  }else throw new AssertionError("Unknown scenario: "+scenario);
  System.out.println("PASS "+scenario);
 }
}
'''


@unittest.skipUnless(ECJ.is_file() and ANDROID.is_file(), 'Retained ECJ and Android SDK required')
class RequestRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='weqrom-request-region-')
        root = Path(cls.temporary.name)
        cls.production, cls.doubles = root / 'production', root / 'doubles'
        cls.production.mkdir()
        cls.doubles.mkdir()
        context = root / 'sources/android/content/Context.java'
        context.parent.mkdir(parents=True)
        context.write_text(CONTEXT)
        harness = root / 'sources/RequestRegionHarness.java'
        harness.write_text(HARNESS)
        compiler = ['java', '-jar', str(ECJ), '-1.8', '-proc:none', '-warn:none']
        subprocess.run([*compiler, '-classpath', str(ANDROID), '-d', str(cls.production),
                        str(SOURCE / 'RegionCatalog.java'), str(SOURCE / 'RequestRegion.java')],
                       check=True, capture_output=True, text=True)
        subprocess.run([*compiler, '-classpath', os.pathsep.join((str(cls.production), str(ANDROID))),
                        '-d', str(cls.doubles), str(context), str(harness)],
                       check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_case(self, scenario, *arguments):
        result = subprocess.run(['java', '-cp', os.pathsep.join((str(self.doubles), str(self.production), str(ANDROID))),
                                 'RequestRegionHarness', scenario, *arguments], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PASS ' + scenario, result.stdout)

    def test_rewrites_six_client_keys_with_numeric_mcc_without_mutating_account_auth_or_input(self):
        self.run_case('copy')

    def test_every_country_uses_matching_iso_mcc_and_mnc_in_maps_and_urls(self):
        self.run_case('all_countries')

    def test_url_duplicates_encoded_keys_and_unrelated_bytes_are_preserved(self):
        self.run_case('url')

    def test_signed_queries_are_preserved_entirely_and_skips_do_not_leak_urls(self):
        self.run_case('url_signed')

    def test_url_null_fragment_path_nested_values_and_malformed_escapes_are_safe(self):
        self.run_case('url_boundaries')

    def test_persisted_server_store_country_is_read_only_separate_from_client_selection(self):
        self.run_case('store_report')

    def test_store_report_missing_malformed_and_unavailable_values_are_unknown_without_leaks(self):
        self.run_case('store_report_unknown')

    def test_dynamic_selection_normalization_rejection_and_honest_observation(self):
        self.run_case('selection')

    def test_missing_keys_and_null_maps_preserve_original_contract(self):
        self.run_case('missing')

    def test_early_context_loads_once_and_requests_never_read_preferences(self):
        self.run_case('early')

    def test_bound_application_store_is_preferred(self):
        self.run_case('bound')

    def test_valid_source_mod_country_falls_back_from_unsupported_own_choice(self):
        self.run_case('fallback_mod')

    def test_invalid_and_malformed_persisted_countries_use_belarus(self):
        self.run_case('fallback_default')

    def test_storage_failure_preserves_choice_and_can_recover_without_leaking_messages(self):
        self.run_case('storage')

    def test_concurrent_country_changes_use_one_snapshot_per_map(self):
        self.run_case('concurrency')

    def test_original_mod_array_index_synchronizes_stored_and_request_country(self):
        self.run_case('advanced')

    def test_selection_persists_canonical_iso_once_and_recovers_from_storage_failure(self):
        self.run_case('persistence')

    def test_original_selector_choice_is_restored_in_a_fresh_process(self):
        with tempfile.TemporaryDirectory(prefix='weqrom-country-reload-') as directory:
            preference = str(Path(directory) / 'region.properties')
            self.run_case('advanced', preference)
            self.run_case('reload_advanced', preference)


if __name__ == '__main__':
    unittest.main()
