"""Execute the real request-map helper, not TikTok or a simulated hook.

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
  int reads,writes;boolean failEdits;
  final SharedPreferences preferences=(SharedPreferences)Proxy.newProxyInstance(
   RequestRegionHarness.class.getClassLoader(),new Class<?>[]{SharedPreferences.class},this);
  public Object invoke(Object proxy,Method method,Object[] args){
   if(method.getName().equals("getString")){reads++;return values.containsKey(args[0])?(String)values.get(args[0]):args[1];}
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
  Context application;int reads;boolean broken;
  final Map<String,Memory> stores=new HashMap<String,Memory>();
  Memory store(String file){if(!stores.containsKey(file))stores.put(file,new Memory());return stores.get(file);}
  public Context getApplicationContext(){return application;}
  public SharedPreferences getSharedPreferences(String file,int mode){
   reads++;check(mode==0,"Non-private storage requested");
   check(file.equals("weqrom")||file.equals("ttmod_settings"),"Unexpected preference file");
   if(broken)throw new IllegalStateException("private-storage-message-token");
   return store(file).preferences;
  }
 }
 static Map<String,Object> input(){
  Map<String,Object> map=new LinkedHashMap<String,Object>();
  map.put("region","RU");map.put("carrier_region","RU");map.put("op_region","RU");
  map.put("account_region","RU");map.put("sys_region","RU");map.put("app_language","ru");
  map.put("token","private-auth-token");return map;
 }
 static void regions(Map map,String country){
  for(String key:new String[]{"region","carrier_region","op_region"})check(country.equals(map.get(key)),"Inconsistent region field: "+key);
 }
 public static void main(String[] args)throws Exception {
  String scenario=args[0];
  if(scenario.equals("copy")){
   Map<String,Object> original=input();Map<String,Object> before=new LinkedHashMap<String,Object>(original);
   HashMap copy=RequestRegion.rewrite(Collections.unmodifiableMap(original));
   check(copy!=original,"Caller map reused");regions(copy,"BY");check(original.equals(before),"Caller map mutated");
   for(String key:new String[]{"account_region","sys_region","app_language","token"})check(original.get(key).equals(copy.get(key)),"Non-client-region field changed: "+key);
   copy.put("new","value");check(!original.containsKey("new"),"Copy still aliases original map");
   String summary=RequestRegion.summary(null);
   check(summary.contains("BY")&&summary.contains("carrier_region")&&summary.contains("op_region"),"Local rewrite omitted from report");
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
   Map<String,Object> map=new HashMap<String,Object>();map.put("account_region","RU");map.put("sys_region","RU");
   HashMap copy=RequestRegion.rewrite(map);check(copy.equals(map)&&copy.size()==2,"Absent regional fields were inserted");
   check(RequestRegion.summary(null).contains("Карт с заменой региона: 0"),"No-region map claimed a rewrite");
   map.put("carrier_region",null);RequestRegion.select("DE");copy=RequestRegion.rewrite(map);
   check("DE".equals(copy.get("carrier_region"))&&!copy.containsKey("region")&&!copy.containsKey("op_region"),"Partial field scope changed");
   check(map.get("carrier_region")==null,"Partial input was mutated");
   check(RequestRegion.rewrite(null)==null,"Null map no longer propagates");
   check(RequestRegion.summary(null).contains("Подготовлено карт параметров: 2"),"Null input was counted as prepared");
  }else if(scenario.equals("early")){
   RequestRegion.initialize(null);BootContext base=new BootContext();
   base.store("weqrom").values.put("region","de");base.store("ttmod_settings").values.put("selected_region_iso","jp");
   check(base.getApplicationContext()==null,"Fixture did not exercise early context");
   RequestRegion.initialize(base);check("DE".equals(RequestRegion.country()),"Own preference was not preferred");
   int reads=base.reads;check(reads==1,"Source preference needlessly read after valid own ISO");
   base.store("weqrom").values.put("region","FR");RequestRegion.initialize(base);
   check(base.reads==reads&&"DE".equals(RequestRegion.country()),"Initialization repeatedly read/reset preferences");
   int stringReads=base.store("weqrom").reads;
   for(int i=0;i<1000;i++)regions(RequestRegion.rewrite(input()),"DE");
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
  }else if(scenario.equals("concurrency")){
   final CountDownLatch begin=new CountDownLatch(1);final AtomicReference<Throwable> failure=new AtomicReference<Throwable>();
   final Map<String,Object> original=Collections.unmodifiableMap(input());List<Thread> threads=new ArrayList<Thread>();
   threads.add(new Thread(new Runnable(){public void run(){try{begin.await();for(int i=0;i<20000;i++)RequestRegion.select((i&1)==0?"JP":"GB");}catch(Throwable error){failure.compareAndSet(null,error);}}}));
   for(int reader=0;reader<4;reader++)threads.add(new Thread(new Runnable(){public void run(){try{
    begin.await();for(int i=0;i<3000;i++){Map result=RequestRegion.rewrite(original);String iso=(String)result.get("region");
     check(iso.equals("BY")||iso.equals("JP")||iso.equals("GB"),"Unexpected concurrent country");regions(result,iso);
     check("RU".equals(result.get("account_region")),"Concurrent rewrite changed account country");}
   }catch(Throwable error){failure.compareAndSet(null,error);}}}));
   for(Thread thread:threads)thread.start();begin.countDown();for(Thread thread:threads)thread.join();
   if(failure.get()!=null)throw new AssertionError("Concurrent selection/rewrite failure",failure.get());
   check(RequestRegion.summary(null).contains("Карт с заменой региона: 12000"),"Concurrent local counter lost prepared maps");
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

    def test_rewrites_three_client_keys_without_mutating_account_auth_or_input(self):
        self.run_case('copy')

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
