#include <jni.h>
#include <llama.h>
#include <mtmd.h>
#include <mtmd-helper.h>
#include "image_resize.h"
#include "generation_control.h"
#include <thread>
#include <optional>
#include <memory>
#include <algorithm>
#include <cstdint>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
std::mutex state_mutex;
llama_model * model = nullptr;
llama_context * ctx = nullptr;
mtmd_context * vision = nullptr;
bool backend_initialized = false;
chits::Cancellation cancellation;

void cleanup() {
    if (vision) mtmd_free(vision);
    vision = nullptr;
    if (ctx) llama_free(ctx);
    if (model) llama_model_free(model);
    ctx = nullptr;
    model = nullptr;
}

void throw_error(JNIEnv * env, const char * message) {
    if (env->ExceptionCheck()) return;
    jclass cls = env->FindClass("java/lang/IllegalStateException");
    if (cls) env->ThrowNew(cls, message);
}

std::string byte_string(JNIEnv * env, jbyteArray input) {
    if (!input) throw std::runtime_error("Empty prompt");
    jsize n = env->GetArrayLength(input);
    if (n > 32768) throw std::runtime_error("Prompt is too long (maximum 32 KiB UTF-8)");
    std::string out(static_cast<size_t>(n), '\0');
    env->GetByteArrayRegion(input, 0, n, reinterpret_cast<jbyte *>(out.data()));
    return out;
}

std::string chat_prompt(const std::string & user) {
    const char * system = "Ты Chits, локальный офлайн-помощник. Отвечай по-русски кратко, обычно 2–4 предложениями. "
                          "Если не знаешь, скажи об этом. Ты не управляешь устройством и не имеешь доступа к интернету. "
                          "На фото описывай только видимое, не выдумывай детали.";
    llama_chat_message messages[] = {{"system", system}, {"user", user.c_str()}};
    const char * templ = llama_model_chat_template(model, nullptr);
    if (!templ) {
        return std::string("<|im_start|>system\n") + system + "<|im_end|>\n<|im_start|>user\n" +
               user + "<|im_end|>\n<|im_start|>assistant\n";
    }
    std::vector<char> out(8192);
    int32_t n = llama_chat_apply_template(templ, messages, 2, true, out.data(), static_cast<int32_t>(out.size()));
    if (n < 0) throw std::runtime_error("Model chat template is unsupported");
    if (n > static_cast<int32_t>(out.size())) {
        out.resize(static_cast<size_t>(n) + 1);
        n = llama_chat_apply_template(templ, messages, 2, true, out.data(), static_cast<int32_t>(out.size()));
        if (n < 0) throw std::runtime_error("Could not apply model chat template");
    }
    return std::string(out.data(), static_cast<size_t>(n));
}

std::string infer(const std::string & user, const std::string & image_path, int max_tokens, chits::TextStream::Callback on_partial = {}, std::optional<uint64_t> request_epoch = std::nullopt) {
    if (!model || !ctx) throw std::runtime_error("Load a GGUF model first");
    cancellation.begin(request_epoch.value_or(cancellation.capture()));
    if (cancellation.requested()) return {};
    max_tokens = std::clamp(max_tokens, 16, 256);
    const llama_vocab * vocab = llama_model_get_vocab(model);
    llama_memory_clear(llama_get_memory(ctx), true);
    llama_pos n_past = 0;
    if (!image_path.empty()) {
        if (!vision) throw std::runtime_error("Photo understanding requires the vision model and its projector");
        std::string content = user;
        for (const std::string reserved : {std::string(mtmd_default_marker()), std::string(MTMD_DEFAULT_IMAGE_MARKER)}) {
            size_t pos = 0;
            while ((pos = content.find(reserved, pos)) != std::string::npos) {
                content.replace(pos, reserved.size(), "[image]");
                pos += 7;
            }
        }
        std::string prompt = chat_prompt(std::string(mtmd_default_marker()) + "\n" + content);
        mtmd::bitmap_ptr original(mtmd_helper_bitmap_init_from_file(vision, image_path.c_str()));
        if (!original || mtmd_bitmap_is_audio(original.get())) throw std::runtime_error("Could not decode the selected photo");
        if (cancellation.requested()) return {};
        auto resized = chits::resize_rgb(mtmd_bitmap_get_data(original.get()), mtmd_bitmap_get_nx(original.get()), mtmd_bitmap_get_ny(original.get()));
        original.reset();
        mtmd::bitmap_ptr bitmap(mtmd_bitmap_init(resized.width, resized.height, resized.pixels.data()));
        mtmd::input_chunks_ptr chunks(mtmd_input_chunks_init());
        if (!bitmap || !chunks) throw std::runtime_error("Could not allocate image input");
        const mtmd_bitmap * bitmaps[] = {bitmap.get()};
        mtmd_input_text text{prompt.c_str(), true, true};
        if (mtmd_tokenize(vision, chunks.get(), &text, bitmaps, 1) != 0)
            throw std::runtime_error("Could not preprocess photo for the vision model");
        // Image temporal positions differ from the number of occupied KV slots.
        if (mtmd_helper_get_n_tokens(chunks.get()) + max_tokens + 4 > llama_n_ctx(ctx))
            throw std::runtime_error("Photo and message exceed the local model context. Use a shorter question.");
        if (cancellation.requested()) return {};
        int status = mtmd_helper_eval_chunks(vision, ctx, chunks.get(), 0, 0, 512, true, &n_past);
        if (cancellation.requested()) return {};
        if (status != 0) throw std::runtime_error("Local photo encoding or prompt evaluation failed");
    } else {
        std::string prompt = chat_prompt(user);
        int32_t count = -llama_tokenize(vocab, prompt.data(), static_cast<int32_t>(prompt.size()), nullptr, 0, true, true);
        if (count <= 0) throw std::runtime_error("Could not tokenize prompt");
        if (count + max_tokens + 4 > static_cast<int32_t>(llama_n_ctx(ctx)))
            throw std::runtime_error("Message is too long for the local model. Use a shorter question.");
        std::vector<llama_token> tokens(static_cast<size_t>(count));
        int32_t actual = llama_tokenize(vocab, prompt.data(), static_cast<int32_t>(prompt.size()), tokens.data(), count, true, true);
        if (actual <= 0) throw std::runtime_error("Could not tokenize prompt");
        tokens.resize(static_cast<size_t>(actual));
        for (size_t offset = 0; offset < tokens.size(); offset += 512) {
            int32_t size = static_cast<int32_t>(std::min<size_t>(512, tokens.size() - offset));
            if (cancellation.requested()) return {};
            llama_batch batch = llama_batch_get_one(tokens.data() + offset, size);
            int status = llama_decode(ctx, batch);
            if (cancellation.requested()) return {};
            if (status != 0) throw std::runtime_error("Local model prompt evaluation failed");
        }
        n_past = actual;
    }
    if (cancellation.requested()) return {};
    llama_sampler * sampler = llama_sampler_chain_init(llama_sampler_chain_default_params());
    if (!sampler) throw std::runtime_error("Could not allocate sampler");
    llama_sampler_chain_add(sampler, llama_sampler_init_top_k(40));
    llama_sampler_chain_add(sampler, llama_sampler_init_top_p(0.9f, 1));
    llama_sampler_chain_add(sampler, llama_sampler_init_temp(0.65f));
    llama_sampler_chain_add(sampler, llama_sampler_init_dist(LLAMA_DEFAULT_SEED));
    chits::TextStream stream(std::move(on_partial));
    llama_batch generation_batch = llama_batch_init(1, 0, 1);
    try {
        for (int i = 0; i < max_tokens; ++i) {
            if (cancellation.requested()) break;
            llama_token token = llama_sampler_sample(sampler, ctx, -1);
            if (llama_vocab_is_eog(vocab, token)) break;
            std::vector<char> piece(256);
            int32_t n = llama_token_to_piece(vocab, token, piece.data(), static_cast<int32_t>(piece.size()), 0, false);
            if (n < 0) {
                piece.resize(static_cast<size_t>(-n));
                n = llama_token_to_piece(vocab, token, piece.data(), static_cast<int32_t>(piece.size()), 0, false);
            }
            if (n < 0) throw std::runtime_error("Could not decode generated token");
            stream.append(piece.data(), static_cast<size_t>(n));
            if (cancellation.requested()) break;
            if (i + 1 < max_tokens) {
                // Continue at the explicit temporal position returned by mtmd,
                // not the image KV-slot count (Qwen uses multidimensional RoPE).
                generation_batch.n_tokens = 1;
                generation_batch.token[0] = token;
                generation_batch.pos[0] = n_past++;
                generation_batch.n_seq_id[0] = 1;
                generation_batch.seq_id[0][0] = 0;
                generation_batch.logits[0] = true;
                int status = llama_decode(ctx, generation_batch);
                if (cancellation.requested()) break;
                if (status != 0) throw std::runtime_error("Local model generation failed");
            }
        }
    } catch (...) {
        llama_batch_free(generation_batch);
        llama_sampler_free(sampler);
        throw;
    }
    llama_batch_free(generation_batch);
    llama_sampler_free(sampler);
    return stream.finish();
}
std::string jni_path(JNIEnv * env, jstring path) {
    if (!path) return {};
    const char * chars = env->GetStringUTFChars(path, nullptr);
    if (!chars) throw std::runtime_error("Could not read local file path");
    std::string out(chars);
    env->ReleaseStringUTFChars(path, chars);
    return out;
}

jboolean load_jni(JNIEnv * env, jstring path, jstring projector_path) {
    std::lock_guard<std::mutex> guard(state_mutex);
    try {
        std::string file = jni_path(env, path);
        std::string projector = jni_path(env, projector_path);
        if (file.empty()) throw std::runtime_error("Missing model path");
        cleanup();
        cancellation.reset();
        if (!backend_initialized) {
            llama_log_set([](ggml_log_level, const char *, void *) {}, nullptr);
            llama_backend_init();
            backend_initialized = true;
        }
        auto mp = llama_model_default_params();
        mp.n_gpu_layers = 0;
        mp.use_mmap = true;
        mp.progress_callback = [](float, void *) { return !cancellation.requested(); };
        mp.progress_callback_user_data = nullptr;
        model = llama_model_load_from_file(file.c_str(), mp);
        if (!model) throw std::runtime_error("Could not load GGUF model (invalid file or insufficient memory)");
        auto cp = llama_context_default_params();
        cp.n_ctx = projector.empty() ? 2048 : 4096;
        cp.n_batch = 512;
        cp.n_ubatch = 128;
        const unsigned int cores = std::thread::hardware_concurrency();
        const int threads = static_cast<int>(std::max(1u, std::min(4u, cores ? cores : 2u)));
        cp.n_threads = threads;
        cp.n_threads_batch = threads;
        cp.no_perf = true;
        cp.abort_callback = [](void *) { return cancellation.requested(); };
        cp.abort_callback_data = nullptr;
        ctx = llama_init_from_model(model, cp);
        if (!ctx) throw std::runtime_error("Could not create model context (insufficient memory)");
        if (!projector.empty()) {
            auto vp = mtmd_context_params_default();
            vp.use_gpu = false;
            vp.print_timings = false;
            vp.n_threads = threads;
            vp.verbosity = GGML_LOG_LEVEL_ERROR;
            vision = mtmd_init_from_file(projector.c_str(), model, vp);
            if (!vision || !mtmd_support_vision(vision))
                throw std::runtime_error("Could not load compatible vision projector (invalid file or insufficient memory)");
        }
        return JNI_TRUE;
    } catch (const std::exception & e) {
        cleanup();
        throw_error(env, e.what());
        return JNI_FALSE;
    }
}
}  // namespace

extern "C" JNIEXPORT jboolean JNICALL
Java_market_chits_OfflineAi_nativeLoad(JNIEnv * env, jclass, jstring path) {
    return load_jni(env, path, nullptr);
}

extern "C" JNIEXPORT jboolean JNICALL
Java_market_chits_OfflineAi_nativeLoadVision(JNIEnv * env, jclass, jstring path, jstring projector_path) {
    return load_jni(env, path, projector_path);
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_market_chits_OfflineAi_nativeGenerate(JNIEnv * env, jclass, jbyteArray prompt, jint max_tokens) {
    std::lock_guard<std::mutex> guard(state_mutex);
    try {
        std::string answer = infer(byte_string(env, prompt), {}, max_tokens);
        jbyteArray result = env->NewByteArray(static_cast<jsize>(answer.size()));
        if (!result) return nullptr;
        env->SetByteArrayRegion(result, 0, static_cast<jsize>(answer.size()), reinterpret_cast<const jbyte *>(answer.data()));
        return result;
    } catch (const std::exception & e) {
        throw_error(env, e.what());
        return nullptr;
    }
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_market_chits_OfflineAi_nativeGenerateVision(JNIEnv * env, jclass, jbyteArray prompt, jstring image_path, jint max_tokens) {
    std::lock_guard<std::mutex> guard(state_mutex);
    try {
        std::string file = jni_path(env, image_path);
        if (file.empty()) throw std::runtime_error("Missing selected photo path");
        std::string answer = infer(byte_string(env, prompt), file, max_tokens);
        jbyteArray result = env->NewByteArray(static_cast<jsize>(answer.size()));
        if (!result) return nullptr;
        env->SetByteArrayRegion(result, 0, static_cast<jsize>(answer.size()), reinterpret_cast<const jbyte *>(answer.data()));
        return result;
    } catch (const std::exception & e) {
        throw_error(env, e.what());
        return nullptr;
    }
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_market_chits_OfflineAi_nativeGenerateStreaming(JNIEnv * env, jclass, jbyteArray prompt,
        jstring image_path, jint max_tokens, jobject adapter, jlong request_epoch) {
    std::lock_guard<std::mutex> guard(state_mutex);
    try {
        chits::TextStream::Callback callback;
        if (adapter) {
            jclass adapter_class = env->GetObjectClass(adapter);
            jmethodID on_bytes = env->GetMethodID(adapter_class, "onBytes", "([B)V");
            env->DeleteLocalRef(adapter_class);
            if (!on_bytes) return nullptr;
            callback = [env, adapter, on_bytes](const std::string & partial) {
                jbyteArray bytes = env->NewByteArray(static_cast<jsize>(partial.size()));
                if (!bytes) throw std::runtime_error("Could not allocate streamed response");
                env->SetByteArrayRegion(bytes, 0, static_cast<jsize>(partial.size()), reinterpret_cast<const jbyte *>(partial.data()));
                env->CallVoidMethod(adapter, on_bytes, bytes);
                env->DeleteLocalRef(bytes);
                if (env->ExceptionCheck()) throw std::runtime_error("Response listener failed");
            };
        }
        std::string answer = infer(byte_string(env, prompt), jni_path(env, image_path), max_tokens, callback, static_cast<uint64_t>(request_epoch));
        jbyteArray result = env->NewByteArray(static_cast<jsize>(answer.size()));
        if (!result) return nullptr;
        env->SetByteArrayRegion(result, 0, static_cast<jsize>(answer.size()), reinterpret_cast<const jbyte *>(answer.data()));
        return result;
    } catch (const std::exception & error) {
        throw_error(env, error.what());
        return nullptr;
    }
}

extern "C" JNIEXPORT jlong JNICALL
Java_market_chits_OfflineAi_nativeCancellationEpoch(JNIEnv *, jclass) {
    return static_cast<jlong>(cancellation.capture());
}

extern "C" JNIEXPORT void JNICALL
Java_market_chits_OfflineAi_nativeCancel(JNIEnv *, jclass) {
    // No state_mutex: decode's abort callback must observe this during inference.
    cancellation.cancel();
}

extern "C" JNIEXPORT void JNICALL
Java_market_chits_OfflineAi_nativeRelease(JNIEnv *, jclass) {
    std::lock_guard<std::mutex> guard(state_mutex);
    cleanup();
}
