"""Execute the real UI preference API before and after Application binding.

WeqromUi.java is compiled unchanged against Android's SDK. The JVM run substitutes
only Context and in-memory SharedPreferences behavior; it does not substitute the
production UI, execute Android views, or claim to launch TikTok on a device.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


TOOLS = Path(os.environ.get('CHITS_TOOLS_DIR', '/workspace/.chits-tools'))
ECJ = TOOLS / 'ecj.jar'
ANDROID = TOOLS / 'platform/android-35/android.jar'
UI = Path(__file__).resolve().parents[1] / 'app/src/main/java/app/weqrom/WeqromUi.java'

CONTEXT = '''package android.content;
/** Only the two Context operations exercised by the real preference API. */
public abstract class Context {
 public abstract Context getApplicationContext();
 public abstract SharedPreferences getSharedPreferences(String name,int mode);
}
'''

HARNESS = '''import app.weqrom.WeqromUi;
import android.content.Context;
import android.content.SharedPreferences;
import java.lang.reflect.*;
import java.util.*;

public class StartupContextHarness {
 static final class Memory implements InvocationHandler {
  final Map<String,Object> values=new HashMap<String,Object>();
  final SharedPreferences preferences=(SharedPreferences)Proxy.newProxyInstance(
   StartupContextHarness.class.getClassLoader(),new Class<?>[]{SharedPreferences.class},this);
  public Object invoke(Object proxy,Method method,Object[] args){
   String name=method.getName();
   if(name.equals("contains"))return values.containsKey(args[0]);
   if(name.equals("edit")){
    final Map<String,Object> pending=new HashMap<String,Object>();
    return Proxy.newProxyInstance(StartupContextHarness.class.getClassLoader(),new Class<?>[]{SharedPreferences.Editor.class},new InvocationHandler(){
     public Object invoke(Object editor,Method operation,Object[] parameters){
      String operationName=operation.getName();
      if(operationName.startsWith("put")){pending.put((String)parameters[0],parameters[1]);return editor;}
      if(operationName.equals("apply")||operationName.equals("commit")){values.putAll(pending);return operationName.equals("commit")?Boolean.TRUE:null;}
      throw new AssertionError("Unexpected editor operation: "+operationName);
     }
    });
   }
   if(name.equals("getString")||name.equals("getInt")||name.equals("getBoolean"))return values.containsKey(args[0])?values.get(args[0]):args[1];
   throw new AssertionError("Unexpected preference operation: "+name);
  }
 }
 static final class BootContext extends Context {
  Context application;
  final Memory memory=new Memory();
  int storeCalls;
  boolean failStorage;
  public Context getApplicationContext(){return application;}
  public SharedPreferences getSharedPreferences(String name,int mode){
   check("weqrom".equals(name)&&mode==0,"Wrong preference file or access mode");
   storeCalls++;
   if(failStorage)throw new IllegalStateException("storage unavailable");
   return memory.preferences;
  }
 }
 static void check(boolean condition,String reason){if(!condition)throw new AssertionError(reason);}
 public static void main(String[] args){
  Locale.setDefault(Locale.US);
  if(args[0].equals("early")){
   BootContext base=new BootContext();
   check(base.getApplicationContext()==null,"Fixture must reproduce attachBaseContext before Application binding");
   check(WeqromUi.preferences(base)==base.memory.preferences,"Early startup did not use supplied base context");
   WeqromUi.initialize(base);
   WeqromUi.Options defaults=WeqromUi.options(base);
   check("BY".equals(defaults.region),"Early startup did not initialize Belarus");
   check("dark".equals(defaults.theme)&&defaults.likeColor==0xffff385d,"Initial appearance defaults missing");
   check("en".equals(defaults.language),"Initial language did not use device locale");
   check(!WeqromUi.isWelcomeComplete(base),"Fresh startup incorrectly skipped welcome");
   check(base.storeCalls>0,"Base context preference store was not used");
  }else if(args[0].equals("bound")){
   BootContext base=new BootContext(),application=new BootContext();
   application.application=application;base.application=application;
   check(WeqromUi.preferences(base)==application.memory.preferences,"Bound Application context was not preferred");
   WeqromUi.initialize(base);
   check(base.storeCalls==0,"Bound startup unnecessarily accessed base storage");
   check("BY".equals(application.memory.values.get("region")),"Application store was not initialized");
   application.failStorage=true;
   try{WeqromUi.preferences(base);throw new AssertionError("Storage failure was masked");}
   catch(IllegalStateException expected){check("storage unavailable".equals(expected.getMessage()),"Wrong storage exception");}
   check(base.storeCalls==0,"Storage failure silently fell back to a different context");
  }else if(args[0].equals("existing")){
   BootContext base=new BootContext();
   base.memory.values.put("region","DE");base.memory.values.put("theme","light");
   base.memory.values.put("like_color",0xff3287ff);base.memory.values.put("language","be");
   base.memory.values.put("welcome_complete",true);base.memory.values.put("hello_revision",3);base.memory.values.put("haptics",true);
   WeqromUi.initialize(base);WeqromUi.initialize(base);
   WeqromUi.Options existing=WeqromUi.options(base);
   check("DE".equals(existing.region)&&"light".equals(existing.theme),"Early startup overwrote existing choices");
   check(existing.likeColor==0xff3287ff&&"be".equals(existing.language)&&existing.haptics,"Existing appearance/language/options changed");
   check(WeqromUi.isWelcomeComplete(base),"Existing welcome completion was overwritten");
  }else throw new AssertionError("Unknown case");
  System.out.println("PASS "+args[0]);
 }
}
'''


@unittest.skipUnless(shutil.which('java') and ECJ.is_file() and ANDROID.is_file(),
                     'Retained Java/ECJ and Android SDK are required')
class StartupContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.production = cls.root / 'production'
        cls.doubles = cls.root / 'doubles'
        context = cls.root / 'android/content/Context.java'
        context.parent.mkdir(parents=True)
        context.write_text(CONTEXT)
        harness = cls.root / 'StartupContextHarness.java'
        harness.write_text(HARNESS)
        compiler = ['java', '-jar', str(ECJ), '-1.8', '-proc:none', '-warn:none']
        # Production UI compiles against the genuine Android SDK, not doubles.
        subprocess.run([*compiler, '-classpath', str(ANDROID), '-d', str(cls.production), *[str(p) for p in sorted(UI.parent.glob('*.java'))]],
                       check=True, capture_output=True, text=True)
        subprocess.run([*compiler, '-classpath', os.pathsep.join((str(cls.production), str(ANDROID))),
                        '-d', str(cls.doubles), str(context), str(harness)],
                       check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_case(self, case):
        result = subprocess.run(['java', '-cp', os.pathsep.join((str(self.doubles), str(self.production), str(ANDROID))),
                                 'StartupContextHarness', case], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PASS ' + case, result.stdout)

    def test_attach_base_context_with_null_application_initializes_real_preferences(self):
        self.run_case('early')

    def test_bound_application_is_preferred_without_masking_storage_failures(self):
        self.run_case('bound')

    def test_early_initialization_preserves_existing_user_options_and_completion(self):
        self.run_case('existing')


if __name__ == '__main__':
    unittest.main()
