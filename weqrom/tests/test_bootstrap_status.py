"""Execute the real nonthrowing recorder with narrow Android/UI API doubles.

Production BootstrapStatus compiles against the real Android SDK. JVM doubles
provide only preference storage and the UI integration slot; no Android runtime,
TikTok hooks, or network behavior is claimed by these tests.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


TOOLS = Path(os.environ.get("CHITS_TOOLS_DIR", "/workspace/.chits-tools"))
ECJ = TOOLS / "ecj.jar"
ANDROID = TOOLS / "platform/android-35/android.jar"
STATUS = Path(__file__).resolve().parents[1] / "app/src/main/java/app/weqrom/BootstrapStatus.java"

UI = """package app.weqrom;
public class WeqromUi {
 public interface Integration { }
 public static Integration integration;
 public static void setIntegration(Integration value){integration=value;}
}
"""

CONTEXT = """package android.content;
public abstract class Context {
 public static final int MODE_PRIVATE=0;
 public abstract Context getApplicationContext();
 public abstract SharedPreferences getSharedPreferences(String name,int mode);
 public ClassLoader getClassLoader(){return getClass().getClassLoader();}
 public android.content.pm.PackageManager getPackageManager(){return null;}
 public String getPackageName(){return "com.zhiliaoapp.musically";}
}
"""

HARNESS = """import android.content.*;
import java.util.*;
import java.lang.reflect.*;
import app.weqrom.BootstrapStatus;
import app.weqrom.WeqromUi;

public class StatusHarness {
 static class Store implements InvocationHandler {
  final Map<String,Object> values=new HashMap<String,Object>();
  final SharedPreferences preferences=(SharedPreferences)Proxy.newProxyInstance(
   getClass().getClassLoader(),new Class<?>[]{SharedPreferences.class},this);
  public Object invoke(Object object,Method method,Object[] args){
   String name=method.getName();
   if(name.equals("contains"))return values.containsKey(args[0]);
   if(name.equals("edit"))return Proxy.newProxyInstance(
    getClass().getClassLoader(),new Class<?>[]{SharedPreferences.Editor.class},this);
   if(name.startsWith("put")){values.put((String)args[0],args[1]);return object;}
   if(name.startsWith("get"))return values.containsKey(args[0])?values.get(args[0]):args[1];
   if(name.equals("apply"))return null;
   throw new AssertionError("Unexpected preference method: "+name);
  }
 }
 static class BootContext extends Context {
  final Store store=new Store();
  Context application;
  int storageCalls;
  boolean broken;
  public Context getApplicationContext(){return application;}
  public SharedPreferences getSharedPreferences(String name,int mode){
   storageCalls++;
   if(broken)throw new IllegalStateException("private-storage-message");
   check(name.equals("weqrom_bootstrap")&&mode==0,"Wrong preference store or mode");
   return store.preferences;
  }
 }
 static void check(boolean condition,String reason){if(!condition)throw new AssertionError(reason);}
 static void setBackend(){WeqromUi.integration=new WeqromUi.Integration(){};}
 public static void main(String[] args){
  BootContext base=new BootContext();
  String scenario=args[0];
  if(scenario.equals("partial")){
   BootstrapStatus.hookError(base,new IllegalStateException("private-hook-message"));
   BootstrapStatus.started(base);
   check(BootstrapStatus.isStarted(base),"Main.start return was not recorded");
   check(BootstrapStatus.hasHooksError(base),"Successful return erased a same-attempt hook failure");
   check(base.store.values.get("hooks").toString().contains("IllegalStateException"),"Hook type was not retained");
   setBackend();
   check(!BootstrapStatus.tryInitializeBridge(base),"Partial startup was advertised as complete");
   check(WeqromUi.integration==null,"Partial startup left an early backend active");
  }else if(scenario.equals("previous")){
   base.store.values.put("time",1L);
   base.store.values.put("started",true);
   base.store.values.put("hooks","");
   check(!BootstrapStatus.isStarted(base),"Previous-process persisted report became current readiness");
   String report=BootstrapStatus.dump(base);
   check(report.contains("Предыдущий сохранённый запуск"),"Historical report was not identified as history");
   check(report.contains("Main.start вернулся: нет"),"Current unconfirmed state was not reported");
   setBackend();
   check(!BootstrapStatus.tryInitializeBridge(base)&&WeqromUi.integration==null,"Unconfirmed startup retained a backend");
  }else if(scenario.equals("privacy")){
   Throwable cause=new UnsupportedOperationException("secret-url?token=456");
   cause.setStackTrace(new StackTraceElement[]{new StackTraceElement("app.example.Worker","load","private-filename-token",42)});
   Throwable wrapper=new IllegalArgumentException("private-token-123",cause);
   BootstrapStatus.failed(base,wrapper);
   String report=BootstrapStatus.dump(base),stored=base.store.values.toString();
   check(report.contains("UnsupportedOperationException")&&report.contains("app.example.Worker.load:42"),"Root cause class/frame was omitted");
   for(String forbidden:new String[]{"private-token-123","secret-url","token=456","private-filename-token"}){
    check(!report.contains(forbidden)&&!stored.contains(forbidden),"Exception messages or filenames leaked");
   }
   check(!BootstrapStatus.isStarted(base),"Failed startup was reported as ready");
  }else if(scenario.equals("context")){
   check(base.getApplicationContext()==null,"Early startup fixture is already bound");
   BootstrapStatus.begin(base);BootstrapStatus.started(base);
   check(base.storageCalls>=2&&Boolean.TRUE.equals(base.store.values.get("started")),"Null Application context did not use base storage");
   BootContext application=new BootContext();application.application=application;base.application=application;
   int oldCalls=base.storageCalls;
   BootstrapStatus.begin(base);BootstrapStatus.started(base);
   check(base.storageCalls==oldCalls&&application.storageCalls>=2,"Bound Application storage was not preferred");
   check(Boolean.TRUE.equals(application.store.values.get("started")),"Bound storage missed the current status");
  }else if(scenario.equals("missing_bridge")){
   BootstrapStatus.begin(base);BootstrapStatus.started(base);setBackend();
   check(!BootstrapStatus.tryInitializeBridge(base),"Missing actual mod class was reported available");
   check(WeqromUi.integration==null,"Failed reflective initialization left an active backend");
   check(BootstrapStatus.hasHooksError(base),"Reflective bridge failure was silently discarded");
   check(BootstrapStatus.dump(base).contains("ClassNotFoundException"),"Reflective failure type missing from diagnostic report");
  }else if(scenario.equals("reset")){
   BootstrapStatus.hookError(base,new RuntimeException("old"));BootstrapStatus.failed(base,new LinkageError("old"));
   BootstrapStatus.begin(base);
   check(!BootstrapStatus.isStarted(base)&&!BootstrapStatus.hasHooksError(base),"New attempt retained old state");
   BootstrapStatus.started(base);
   check(BootstrapStatus.isStarted(base)&&!BootstrapStatus.hasHooksError(base),"Successful new attempt still reports old errors");
  }else if(scenario.equals("nonthrowing")){
   base.broken=true;
   BootstrapStatus.begin(base);
   BootstrapStatus.hookError(base,new LinkageError("private"));
   BootstrapStatus.started(base);
   check(BootstrapStatus.isStarted(base)&&BootstrapStatus.hasHooksError(base),"Storage failure lost in-memory partial status");
   BootstrapStatus.failed(base,new IllegalStateException("private"));
   check(!BootstrapStatus.isStarted(base),"Storage failure lost in-memory startup failure");
   BootstrapStatus.begin(null);BootstrapStatus.hookError(null,null);
   BootstrapStatus.started(null);BootstrapStatus.failed(null,null);
   check(!BootstrapStatus.isStarted(null),"Null-context failure was ignored");
  }else throw new AssertionError("Unknown scenario");
  System.out.println("PASS "+scenario);
 }
}
"""


@unittest.skipUnless(shutil.which("java") and ECJ.is_file() and ANDROID.is_file(),
                     "Retained Java/ECJ and Android SDK are required")
class BootstrapStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="weqrom-status-tests-")
        cls.root = Path(cls.temporary.name)
        cls.production, cls.runtime = cls.root / "production", cls.root / "runtime"
        ui = cls.root / "sources/app/weqrom/WeqromUi.java"
        context = cls.root / "sources/android/content/Context.java"
        harness = cls.root / "sources/StatusHarness.java"
        ui.parent.mkdir(parents=True)
        context.parent.mkdir(parents=True)
        ui.write_text(UI)
        context.write_text(CONTEXT)
        harness.write_text(HARNESS)
        compiler = ["java", "-jar", str(ECJ), "-1.8", "-proc:none", "-warn:none"]
        subprocess.run([*compiler, "-classpath", str(ANDROID), "-d", str(cls.production), str(STATUS), str(ui)],
                       check=True, capture_output=True, text=True)
        subprocess.run([*compiler, "-classpath", os.pathsep.join((str(cls.production), str(ANDROID))),
                        "-d", str(cls.runtime), str(context), str(harness)],
                       check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_scenario(self, scenario):
        result = subprocess.run(["java", "-cp", os.pathsep.join((str(self.runtime), str(self.production), str(ANDROID))),
                                 "StatusHarness", scenario], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS " + scenario, result.stdout)

    def test_successful_return_keeps_same_attempt_hook_error_and_clears_backend(self):
        self.run_scenario("partial")

    def test_previous_process_report_is_history_not_current_readiness(self):
        self.run_scenario("previous")

    def test_exception_root_class_and_frame_are_saved_without_messages_or_filenames(self):
        self.run_scenario("privacy")

    def test_null_application_context_falls_back_and_bound_application_is_preferred(self):
        self.run_scenario("context")

    def test_missing_real_bridge_is_reported_and_early_backend_is_cleared(self):
        self.run_scenario("missing_bridge")

    def test_new_attempt_clears_previous_failure_and_partial_state(self):
        self.run_scenario("reset")

    def test_recording_does_not_throw_for_broken_storage_null_context_or_null_failure(self):
        self.run_scenario("nonthrowing")


if __name__ == "__main__":
    unittest.main()
