// Test-only JNI backend counts lifecycle calls; it does not simulate inference.
#include <jni.h>
#include <atomic>
#include <string>
static std::atomic<int> loads{0};
static std::atomic<int> cancels{0};
static jboolean load(JNIEnv * env, jstring path) {
    ++loads;
    const char * chars = env->GetStringUTFChars(path, nullptr);
    std::string file(chars);
    env->ReleaseStringUTFChars(path, chars);
    return file.find("reject") == std::string::npos ? JNI_TRUE : JNI_FALSE;
}
extern "C" JNIEXPORT jboolean JNICALL Java_market_chits_OfflineAi_nativeLoad(JNIEnv * env, jclass, jstring path) { return load(env, path); }
extern "C" JNIEXPORT jboolean JNICALL Java_market_chits_OfflineAi_nativeLoadVision(JNIEnv * env, jclass, jstring path, jstring) { return load(env, path); }
extern "C" JNIEXPORT void JNICALL Java_market_chits_OfflineAi_nativeRelease(JNIEnv *, jclass) {}
extern "C" JNIEXPORT void JNICALL Java_market_chits_OfflineAi_nativeCancel(JNIEnv *, jclass) { ++cancels; }
extern "C" JNIEXPORT jint JNICALL Java_market_chits_ModelCacheTest_loads(JNIEnv *, jclass) { return loads.load(); }
extern "C" JNIEXPORT jint JNICALL Java_market_chits_ModelCacheTest_cancels(JNIEnv *, jclass) { return cancels.load(); }
