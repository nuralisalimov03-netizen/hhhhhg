"""Run the real theme bridge on JVM with a pinned-binary-name notifier fixture.

These checks exercise persistence, failure rollback and one-shot recreation;
they do not claim to execute TikTok or Android's AppCompat runtime.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ECJ = Path('/workspace/.chits-tools/ecj.jar')


@unittest.skipUnless(ECJ.exists(), 'Retained ECJ compiler is required')
class FeedThemeTests(unittest.TestCase):
    def test_original_notifier_routing_noop_invalid_mode_and_rollback(self):
        sources = {
            'android/content/SharedPreferences.java': '''package android.content;
public interface SharedPreferences {
 boolean contains(String key); int getInt(String key,int fallback); Editor edit();
 interface Editor { Editor putInt(String key,int value); Editor remove(String key); void apply(); }
}''',
            'android/content/Context.java': '''package android.content;
import java.util.HashMap;
public class Context {
 public static final int MODE_PRIVATE=0;
 private final HashMap<String,Memory> files=new HashMap<String,Memory>();
 public ClassLoader getClassLoader(){ return getClass().getClassLoader(); }
 public SharedPreferences getSharedPreferences(String name,int mode){
  if(!files.containsKey(name))files.put(name,new Memory()); return files.get(name);
 }
 private static class Memory implements SharedPreferences,SharedPreferences.Editor {
  private final HashMap<String,Integer> values=new HashMap<String,Integer>();
  public boolean contains(String key){return values.containsKey(key);}
  public int getInt(String key,int fallback){return contains(key)?values.get(key):fallback;}
  public SharedPreferences.Editor edit(){return this;}
  public SharedPreferences.Editor putInt(String key,int value){values.put(key,value);return this;}
  public SharedPreferences.Editor remove(String key){values.remove(key);return this;}
  public void apply(){}
 }
}''',
            'android/app/Activity.java': '''package android.app;
public class Activity extends android.content.Context {
 public boolean finishing,destroyed; public int recreations;
 public boolean isFinishing(){return finishing;}
 public boolean isDestroyed(){return destroyed;}
 public void recreate(){recreations++;}
}''',
            'android/os/Looper.java': '''package android.os;
public class Looper {
 private static final Looper MAIN=new Looper(); public static boolean worker;
 public static Looper getMainLooper(){return MAIN;}
 public static Looper myLooper(){return worker?null:MAIN;}
}''',
            'android/os/Handler.java': '''package android.os;
import java.util.ArrayList;
public class Handler {
 public static final ArrayList<Runnable> pending=new ArrayList<Runnable>();
 public Handler(Looper looper){}
 public boolean postDelayed(Runnable task,long delay){pending.add(task);return true;}
 public static void drain(){for(Runnable r:new ArrayList<Runnable>(pending))r.run();pending.clear();}
}''',
            'X/C005C.java': '''package X;
public class C005C {
 public static int calls,lastValue; public static boolean fail;
 public static void LJJIII(int value){
  if(fail)throw new IllegalStateException("fixture notifier failure");
  calls++;lastValue=value;
 }
}''',
            'ThemeHarness.java': '''import android.app.Activity;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;
import app.weqrom.FeedTheme;
public class ThemeHarness {
 static void check(boolean condition,String description){if(!condition)throw new AssertionError(description);}
 static int field(Class<?> owner,String name)throws Exception{return owner.getField(name).getInt(null);}
 public static void main(String[] args)throws Exception {
  Class<?> notifier=Class.forName("X.005C");
  Activity activity=new Activity();
  SharedPreferences p=activity.getSharedPreferences("sp_dark_mode",0);
  check(FeedTheme.available(activity),"verified binary notifier available");
  check(FeedTheme.apply(activity,"light"),"default light remains selected");
  check(field(notifier,"calls")==0 && Handler.pending.isEmpty(),"unchanged theme is a no-op");
  check(!FeedTheme.apply(activity,"violet"),"unknown mode rejected");
  check(FeedTheme.apply(activity,"dark"),"dark selected");
  check(p.getInt("dark_mode",0)==2 && field(notifier,"lastValue")==2,"real host preference and notifier get dark2");
  check(Handler.pending.size()==1,"one-shot recreation scheduled");
  check(FeedTheme.apply(activity,"dark"),"same dark allowed");
  check(field(notifier,"calls")==1 && Handler.pending.size()==1,"same dark does not add work");
  check(FeedTheme.apply(activity,"system"),"system selected");
  check(p.getInt("dark_mode",0)==-1,"system maps to minus1");
  Handler.drain();
  check(activity.recreations==1,"only latest rapid change recreates");
  notifier.getField("fail").setBoolean(null,true);
  check(!FeedTheme.apply(activity,"light"),"notifier failure reported");
  check(p.getInt("dark_mode",0)==-1 && Handler.pending.isEmpty(),"failed change rolls back preference and schedules nothing");
  notifier.getField("fail").setBoolean(null,false);
  Looper.worker=true;
  check(!FeedTheme.apply(activity,"light") && p.getInt("dark_mode",0)==-1,"worker call rejected without writes");
  Looper.worker=false;
  check(FeedTheme.apply(activity,"light"),"light1 selected");
  check(field(notifier,"lastValue")==1,"light maps to1");
  activity.destroyed=true; Handler.drain();
  check(activity.recreations==1,"destroyed activity is not recreated");
  check(!FeedTheme.apply(activity,"dark"),"destroyed owner rejected");
  System.out.println("Theme notifier/persistence/rollback/lifecycle checks passed");
 }
}''',
        }
        with tempfile.TemporaryDirectory(prefix='weqrom-theme-test-') as directory:
            base = Path(directory)
            files = []
            for relative, content in sources.items():
                path = base / 'sources' / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                files.append(str(path))
            classes = base / 'classes'
            classes.mkdir()
            result = subprocess.run(['java', '-jar', str(ECJ), '-1.8', '-proc:none', '-warn:none',
                                     '-d', str(classes), *files,
                                     str(ROOT/'integration/src/app/weqrom/FeedTheme.java')],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            # JVM binary identifiers may start with a digit; Java source names
            # cannot. Rename the fixture's constant-pool UTF8 class name only.
            original = classes / 'X/C005C.class'
            data = original.read_bytes()
            old = b'\x00\x07X/C005C'
            self.assertEqual(data.count(old), 1)
            (classes/'X/005C.class').write_bytes(data.replace(old, b'\x00\x06X/005C'))
            original.unlink()
            result = subprocess.run(['java', '-cp', str(classes), 'ThemeHarness'],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('checks passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
