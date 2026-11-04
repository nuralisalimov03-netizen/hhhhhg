"""Run the real bridge on the JVM with narrow Android/mod API doubles.

This verifies migration and live setter calls, not Android hooks or TikTok's
servers. APK compilation separately checks these descriptors against the DEX.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCES = {
    'android/content/SharedPreferences.java': '''package android.content;
public interface SharedPreferences {
 int getInt(String k,int d); String getString(String k,String d); boolean getBoolean(String k,boolean d);
 Editor edit(); interface Editor { Editor putInt(String k,int v); Editor putString(String k,String v);
 Editor putBoolean(String k,boolean v); void apply(); }
}''',
    'android/content/Context.java': '''package android.content;
public abstract class Context { public static final int MODE_PRIVATE=0;
 public abstract SharedPreferences getSharedPreferences(String name,int mode); }
''',
    'android/content/ContextWrapper.java': '''package android.content;
public class ContextWrapper extends Context { private Context base;
 public ContextWrapper(Context base){this.base=base;} public Context getBaseContext(){return base;}
 public SharedPreferences getSharedPreferences(String name,int mode){return base.getSharedPreferences(name,mode);} }
''',
    'android/app/Activity.java': '''package android.app;
public class Activity extends android.content.ContextWrapper {
 public android.content.Intent lastIntent;
 public Activity(android.content.Context c){super(c);} public boolean isFinishing(){return false;}
 public boolean isDestroyed(){return false;} public void startActivity(android.content.Intent i){lastIntent=i;} }
''',
    'android/content/Intent.java': '''package android.content;
public class Intent { public String className;
 public Intent setClassName(Context context,String name){className=name;return this;} }
''',
    'android/view/View.java': '''package android.view;
public class View { private android.content.Context c; public View(android.content.Context c){this.c=c;}
 public android.content.Context getContext(){return c;} }
''',
    'android/util/Log.java': '''package android.util;
public class Log { public static int w(String tag,String message){return 0;} }
''',
    'Z/te.java': '''package Z;
public class te { public String c,d,e; public te(String iso){c=iso;d=iso;e="operator";} }
''',
    'Z/ue.java': '''package Z;
public class ue { public static te[] a={new te("by"),new te("us"),new te("kz")}; }
''',
    'Z/ac.java': '''package Z;
public class ac { public static bc cached;
 public static void a(android.content.Context c){if(cached==null)cached=new bc(c.getSharedPreferences("ttmod_settings",0));}
 public static bc e(){return cached;} }
''',
    'Z/bc.java': '''package Z;
import android.content.SharedPreferences;
public class bc { public int b=1,r; public boolean H=true,q,F=true,E=true; public int regionWrites,colorWrites;
 public boolean c,d,e,f,h,i,j,k,p,z; public int featureWrites;
 private SharedPreferences prefs; public bc(SharedPreferences preferences){prefs=preferences;}
 public void c(int i){b=i;regionWrites++;prefs.edit().putInt("selected_region",i).putString("selected_region_iso",ue.a[i].d).apply();}
 public void e(boolean v){F=v;prefs.edit().putBoolean("auto_updates_enable",v).apply();}
 public void m(boolean v){E=v;prefs.edit().putBoolean("statistics_enable",v).apply();}
 public void a(int v){r=v;colorWrites++;prefs.edit().putInt("accent_color",v).apply();}
 public void f(boolean v){q=v;prefs.edit().putBoolean("custom_colors_enabled",v).apply();}
 private void feature(String key,boolean value){featureWrites++;prefs.edit().putBoolean(key,value).apply();}
 public void g(boolean v){d=v;feature("hide_ads",v);}
 public void h(boolean v){c=v;feature("hide_lives",v);}
 public void i(boolean v){e=v;feature("hide_photos",v);}
 public void j(boolean v){j=v;feature("hide_stories",v);}
 public void k(boolean v){k=v;feature("simple_mode",v);}
 public void a(boolean v){p=v;feature("always_show_progress_bar",v);}
 public void l(boolean v){z=v;feature("skip_quality_picker",v);}
 public void c(boolean v){f=v;feature("anti_burnout",v);}
 public void b(boolean v){h=v;feature("anti_burnout_enable_on_start",v);}
 public void d(boolean v){i=v;feature("anti_burnout_hide_system_bars",v);}
 public void b(){H=true;prefs.edit().putBoolean("startup_consent_accepted",true).apply();} }
''',
    'Z/f3.java': '''package Z;
public class f3 { public static java.lang.ref.WeakReference f; }
''',
    'app/weqrom/RequestRegion.java': '''package app.weqrom;
public class RequestRegion { public static String selected="BY";
 public static void initialize(android.content.Context context){}
 public static void select(String iso){selected=iso;}
}''',
    'app/weqrom/FeedTheme.java': '''package app.weqrom;
public class FeedTheme { public static int calls;
 public static boolean fail;
 public static boolean available(android.content.Context c){return true;}
 public static boolean apply(android.app.Activity a,String t){calls++;return !fail;} }
''',
    'app/weqrom/WeqromUi.java': '''package app.weqrom;
import android.content.*;import android.app.Activity;
public class WeqromUi { public static Integration backend; public static Activity lastSettings;
 public interface Integration { void apply(Activity a,Options o);void openAdvanced(Activity a);boolean featureEnabled(Activity a,String key);void setFeature(Activity a,String key,boolean enabled);boolean hasRegionHook();boolean hasThemeHook();boolean hasLikeColorHook(); }
 public static class Options { public String region="US",theme="light",language="ru"; public int likeColor=0xff3287ff; }
 public static void initialize(Context c){}
 public static SharedPreferences preferences(Context c){return c.getSharedPreferences("weqrom",0);}
 public static void setIntegration(Integration i){backend=i;}
 public static boolean isWelcomeComplete(Context c){return preferences(c).getBoolean("welcome_complete",false);}
 public static void showWelcome(Activity a,Runnable done){preferences(a).edit().putBoolean("welcome_complete",true).apply();done.run();}
 public static void showSettings(Activity a){lastSettings=a;} }
''',
    'app/weqrom/BridgeHarness.java': '''package app.weqrom;
import android.content.*;import android.app.Activity;import java.util.*;
public class BridgeHarness {
 static class Preferences implements SharedPreferences,SharedPreferences.Editor {
  Map<String,Object> values=new HashMap<String,Object>();
  public int getInt(String k,int d){return values.containsKey(k)?(Integer)values.get(k):d;}
  public String getString(String k,String d){return values.containsKey(k)?(String)values.get(k):d;}
  public boolean getBoolean(String k,boolean d){return values.containsKey(k)?(Boolean)values.get(k):d;}
  public Editor edit(){return this;} public Editor putInt(String k,int v){values.put(k,v);return this;}
  public Editor putString(String k,String v){values.put(k,v);return this;}
  public Editor putBoolean(String k,boolean v){values.put(k,v);return this;} public void apply(){} }
 static class TestContext extends Context { Map<String,Preferences> stores=new HashMap<String,Preferences>();
  public SharedPreferences getSharedPreferences(String n,int mode){if(!stores.containsKey(n))stores.put(n,new Preferences());return stores.get(n);} }
 static void check(boolean b,String message){if(!b)throw new AssertionError(message);}
 public static void main(String[] args){
  TestContext context=new TestContext(); Activity activity=new Activity(context);
  SharedPreferences own=WeqromUi.preferences(context),mod=context.getSharedPreferences("ttmod_settings",0);
  Z.ac.a(context); Z.bc settings=Z.ac.e(); settings.r=0xff3287ff;
  mod.edit().putString("selected_region_iso","us").putBoolean("startup_consent_accepted",true).apply();
  ModIntegration.initialize(context);
  check(settings.b==0,"Migration did not set live Belarus index");
  check("by".equals(mod.getString("selected_region_iso","")),"Stored Belarus missing");
  check("BY".equals(own.getString("region","")),"UI differs from actual mod country");
  check(!settings.F&&!settings.E&&!settings.H,"Migration flags inconsistent");
  check(!mod.getBoolean("auto_updates_enable",true)&&!mod.getBoolean("statistics_enable",true),"Migration defaults not persisted");
  check(!mod.getBoolean("startup_consent_accepted",true),"Legacy welcome gate not persisted");
  check(WeqromUi.backend.hasRegionHook()&&WeqromUi.backend.hasLikeColorHook(),"Actual hooks not advertised");
  own.edit().putString("region","KZ").apply();
  mod.edit().putString("selected_region_iso","kz").putInt("selected_region",2).apply();
  ModIntegration.initialize(context);
  check(settings.b==2&&"KZ".equals(RequestRegion.selected),"Pending stored region was overwritten by stale live cache");
  ModIntegration.setRegion(context,"us");settings.e(true);settings.m(true);
  int writes=settings.regionWrites;
  ModIntegration.setRegion(context,"US");
  check(settings.regionWrites==writes,"Unchanged country needlessly invoked live setter");
  ModIntegration.initialize(context);ModIntegration.initialize(context);
  check(settings.b==1&&settings.regionWrites==writes,"Later launch overwrote selected country");
  check(settings.F&&settings.E,"Later launch overwrote explicit settings");
  try{ModIntegration.setRegion(context,"RU");throw new AssertionError("Unsupported country accepted");}catch(IllegalArgumentException expected){}
  check(settings.b==1,"Rejected country changed live state");
  ModIntegration.setAccent(context,0xff3287ff);ModIntegration.setAccent(context,0xff3287ff);
  check(settings.q&&settings.r==0xff3287ff,"Actual accent hook not enabled");
  check(settings.colorWrites==0,"Unchanged stored color needlessly rewritten");
  try{ModIntegration.setAccent(context,0x003287ff);throw new AssertionError("Transparent color accepted");}catch(IllegalArgumentException expected){}
  WeqromUi.backend.apply(activity,new WeqromUi.Options());
  check(FeedTheme.calls==0,"Theme recreated host during welcome");
  ModIntegration.showWelcome(activity);
  check(settings.H&&own.getBoolean("welcome_complete",false),"Welcome completion not synchronized");
  WeqromUi.backend.apply(activity,new WeqromUi.Options());
  check(FeedTheme.calls==1,"Completed welcome did not apply theme");
  ModIntegration.initialize(context);check(settings.H,"Completed welcome reset on next initialize");
  FeedTheme.fail=true;WeqromUi.Options failing=new WeqromUi.Options();
  failing.region="KZ";failing.likeColor=0xff9bff75;
  try{WeqromUi.backend.apply(activity,failing);throw new AssertionError("Theme failure silently accepted");}catch(IllegalStateException expected){}
  check(settings.b==2&&settings.r==0xff9bff75,"Theme failure prevented independent country/accent changes");
  check(!WeqromUi.backend.hasThemeHook(),"Failed theme hook still advertised");
  WeqromUi.backend.openAdvanced(activity);
  check(activity.lastIntent!=null&&"Z.sf".equals(activity.lastIntent.className),"Advanced settings did not route to checked original Activity");
  ModIntegration.showSettingsFromArgs(new Object[]{new ContextWrapper(activity)});
  check(WeqromUi.lastSettings==activity,"Context wrapper settings entry failed");
  WeqromUi.lastSettings=null;Z.f3.f=new java.lang.ref.WeakReference<Activity>(activity);
  ModIntegration.showSettingsFromArgs(new Object[]{new Object()});
  check(WeqromUi.lastSettings==activity,"Original lifecycle Activity fallback failed");
  check(ModIntegration.regions().size()==3,"Actual country table not used");
  settings.r=0;settings.q=false;int colorWrites=settings.colorWrites;
  ModIntegration.setAccent(context,0xffff385d);
  check(settings.r==0&&!settings.q&&settings.colorWrites==colorWrites,"Default red needlessly enabled global recoloring");
  check(ModIntegration.featureKeys().length==10,"Expected checked original feature list");
  for(String key:ModIntegration.featureKeys()){
   WeqromUi.backend.setFeature(activity,key,true);
   check(WeqromUi.backend.featureEnabled(activity,key),"Native panel did not read actual feature cache: "+key);
   check(ModIntegration.featureEnabled(context,key)&&mod.getBoolean(key,false),"Feature did not update actual cache/preferences: "+key);
   int featureWrites=settings.featureWrites;ModIntegration.setFeature(context,key,true);
   check(settings.featureWrites==featureWrites,"Unchanged feature called setter again: "+key);
   ModIntegration.setFeature(context,key,false);
   check(!ModIntegration.featureEnabled(context,key)&&!mod.getBoolean(key,true),"Feature did not disable: "+key);
  }
  try{ModIntegration.setFeature(context,"fake_fast_network",true);throw new AssertionError("Unknown feature accepted");}catch(IllegalArgumentException expected){}
  mod.edit().putBoolean("hide_ads",true).apply();ModIntegration.applySavedFeatures(context);
  check(settings.d,"Independent launcher preference did not reach live hook cache");
  int featureWrites=settings.featureWrites;ModIntegration.initialize(context);
  check(settings.d&&settings.featureWrites==featureWrites,"Later initialize reset feature or rewrote unchanged settings");
  System.out.println("Bridge migration, live setter, persistence, welcome, theme guard and settings entry passed");
 }
}''',
}

ECJ = Path('/workspace/.chits-tools/ecj.jar')


class ModIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('java') and (shutil.which('javac') or ECJ.is_file()), 'Java compiler/runtime required for bridge behavior test')
    def test_actual_bridge_migration_and_checked_api_calls(self):
        bridge = Path(__file__).resolve().parents[1] / 'integration/src/app/weqrom/ModIntegration.java'
        catalog = bridge.parents[4] / 'app/src/main/java/app/weqrom/RegionCatalog.java'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = []
            for name, content in SOURCES.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                files.append(str(target))
            compiled = root / 'classes'
            if shutil.which('javac'):
                compiler = ['javac', '-source', '8', '-target', '8', '-Xlint:-options']
            else:
                compiler = ['java', '-jar', str(ECJ), '-8', '-proc:none', '-warn:none']
            subprocess.run([*compiler, '-d', str(compiled), str(bridge), str(catalog), *files], check=True, capture_output=True, text=True)
            result = subprocess.run(['java', '-cp', str(compiled), 'app.weqrom.BridgeHarness'], check=True, capture_output=True, text=True)
            self.assertIn('passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
