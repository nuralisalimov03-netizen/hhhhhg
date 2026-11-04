#include "engine.h"
#include <jni.h>
#include <mutex>
#include <stdexcept>

namespace {
weqrom::Engine engine;
std::mutex engine_mutex;

void error(JNIEnv * env, const char * text) {
    if (env->ExceptionCheck()) return;
    // ThrowNew takes modified UTF-8; error strings here are BMP text only.
    auto cls = env->FindClass("java/lang/IllegalStateException");
    if (cls) env->ThrowNew(cls, text);
}
jbyteArray bytes(JNIEnv * env, const std::string & text) {
    auto result = env->NewByteArray(static_cast<jsize>(text.size()));
    if (result) env->SetByteArrayRegion(result, 0, static_cast<jsize>(text.size()), reinterpret_cast<const jbyte *>(text.data()));
    return result;
}
}

extern "C" JNIEXPORT jboolean JNICALL
Java_app_weqrom_ai_OfflineAi_nativeLoad(JNIEnv * env, jclass, jstring path) {
    std::lock_guard<std::mutex> lock(engine_mutex);
    try {
        if (!path) throw std::runtime_error("Нет пути к встроенной модели.");
        const char * raw = env->GetStringUTFChars(path, nullptr);
        if (!raw) return JNI_FALSE;
        std::string local_path(raw);
        env->ReleaseStringUTFChars(path, raw);
        engine.load(local_path);
        return JNI_TRUE;
    } catch (const std::exception & failure) { error(env, failure.what()); return JNI_FALSE; }
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_app_weqrom_ai_OfflineAi_nativeGenerate(JNIEnv * env, jclass, jbyteArray prompt, jint max_tokens, jobject listener, jlong epoch) {
    std::lock_guard<std::mutex> lock(engine_mutex);
    try {
        if (!prompt || env->GetArrayLength(prompt) > 32768) throw std::runtime_error("Запрос слишком большой.");
        std::string input(static_cast<size_t>(env->GetArrayLength(prompt)), '\0');
        env->GetByteArrayRegion(prompt, 0, static_cast<jsize>(input.size()), reinterpret_cast<jbyte *>(input.data()));
        weqrom::TextStream::Callback callback;
        if (listener) {
            auto cls = env->GetObjectClass(listener);
            auto on_bytes = env->GetMethodID(cls, "onBytes", "([B)V");
            env->DeleteLocalRef(cls);
            if (!on_bytes) return nullptr;
            callback = [env, listener, on_bytes](const std::string & text) {
                auto partial = bytes(env, text);
                if (!partial) throw std::runtime_error("Не хватает памяти для ответа.");
                env->CallVoidMethod(listener, on_bytes, partial);
                env->DeleteLocalRef(partial);
                if (env->ExceptionCheck()) throw std::runtime_error("Не удалось обновить ответ.");
            };
        }
        return bytes(env, engine.generate(input, max_tokens, static_cast<uint64_t>(epoch), std::move(callback)));
    } catch (const std::exception & failure) { error(env, failure.what()); return nullptr; }
}

extern "C" JNIEXPORT jlong JNICALL
Java_app_weqrom_ai_OfflineAi_nativeEpoch(JNIEnv *, jclass) { return static_cast<jlong>(engine.epoch()); }

extern "C" JNIEXPORT void JNICALL
Java_app_weqrom_ai_OfflineAi_nativeCancel(JNIEnv *, jclass) { engine.cancel(); }

extern "C" JNIEXPORT void JNICALL
Java_app_weqrom_ai_OfflineAi_nativeRelease(JNIEnv *, jclass) {
    std::lock_guard<std::mutex> lock(engine_mutex);
    engine.release();
}
