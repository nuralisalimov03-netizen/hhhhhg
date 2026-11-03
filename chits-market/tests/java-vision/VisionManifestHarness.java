package market.chits;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import org.json.JSONArray;
import org.json.JSONObject;

/** Runs production metadata selection/validation; no model, JNI, Android device or network. */
public final class VisionManifestHarness {
    private static final String REV = "0123456789012345678901234567890123456789";
    private static final String HASH = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    private static JSONObject file(String name, long bytes) {
        return new JSONObject().put("path",name).put("size",bytes).put("lfs",new JSONObject().put("oid",HASH));
    }
    private static JSONArray tree() {
        return new JSONArray().put(file("Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf",2100000000L))
            .put(file("mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf",1300000000L));
    }
    private static Object call(String name, Class<?>[] types, Object... args) throws Exception {
        Method method = VisionModelManager.class.getDeclaredMethod(name,types);
        method.setAccessible(true);
        try { return method.invoke(null,args); }
        catch(InvocationTargetException error) {
            Throwable cause=error.getCause();
            if(cause instanceof Exception)throw (Exception)cause;
            throw error;
        }
    }
    private static JSONObject select(JSONArray files) throws Exception {
        return (JSONObject)call("selectManifest",new Class<?>[]{String.class,JSONArray.class},REV,files);
    }
    private static void rejected(JSONArray files) throws Exception {
        try {select(files);}catch(Exception expected){return;}
        throw new AssertionError("Invalid official metadata was accepted");
    }
    public static void main(String[] args) throws Exception {
        String mode=args[0];
        if(mode.equals("official-pair")) {
            JSONObject result=select(tree());
            if(!result.getString("revision").equals(REV))throw new AssertionError("Revision not pinned");
            if(!result.getJSONObject("language").getString("sha256").equals(HASH))throw new AssertionError("LFS hash lost");
            if(result.getJSONObject("language").getLong("size")+result.getJSONObject("projector").getLong("size")!=3400000000L)throw new AssertionError("3GB metadata overflow");
        } else if(mode.equals("prefer-q8-projector")) {
            JSONArray files=tree().put(file("mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf",800000000));
            if(!select(files).getJSONObject("projector").getString("path").contains("Q8_0"))throw new AssertionError("Did not prefer Q8");
        } else if(mode.equals("reject-ambiguous-language")) {
            rejected(tree().put(file("other-Q4_K_M.gguf",500000000)));
        } else if(mode.equals("reject-missing-projector")) {
            rejected(new JSONArray().put(file("Qwen-Q4_K_M.gguf",100000000)));
        } else if(mode.equals("reject-unknown-projector")) {
            rejected(new JSONArray().put(file("Qwen-Q4_K_M.gguf",100000000)).put(file("mmproj-Q2_K.gguf",100000000)));
        } else if(mode.equals("reject-missing-lfs-hash")) {
            JSONArray files=tree();files.getJSONObject(0).remove("lfs");rejected(files);
        } else if(mode.equals("reject-bad-hash")) {
            JSONArray files=tree();files.getJSONObject(1).getJSONObject("lfs").put("oid","not-a-sha256");rejected(files);
        } else if(mode.equals("reject-oversize")) {
            JSONArray files=tree();files.getJSONObject(0).put("size",5L*1024*1024*1024);rejected(files);
        } else if(mode.equals("reject-total-oversize")) {
            JSONArray files=tree();files.getJSONObject(0).put("size",4L*1024*1024*1024);files.getJSONObject(1).put("size",3L*1024*1024*1024);rejected(files);
        } else if(mode.equals("reject-cached-traversal")) {
            JSONObject manifest=select(tree());manifest.getJSONObject("language").put("path","../escape-Q4_K_M.gguf");
            try {call("validateManifest",new Class<?>[]{JSONObject.class},manifest);throw new AssertionError("Traversal accepted");}catch(java.io.IOException expected){}
        } else if(mode.equals("reject-wrong-repo")) {
            JSONObject manifest=select(tree());manifest.put("repo","attacker/wrong-model");
            try {call("validateManifest",new Class<?>[]{JSONObject.class},manifest);throw new AssertionError("Wrong repo accepted");}catch(java.io.IOException expected){}
        } else if(mode.equals("reject-unpinned-revision")) {
            try {call("selectManifest",new Class<?>[]{String.class,JSONArray.class},"main",tree());throw new AssertionError("Unpinned revision accepted");}catch(java.io.IOException expected){}
        } else throw new AssertionError("Unknown test case");
        System.out.println("PASS "+mode);
    }
}
