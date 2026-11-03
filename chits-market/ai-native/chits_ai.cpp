#include <jni.h>
#include <llama.h>
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
bool backend_initialized = false;

void cleanup() {
    if (ctx) llama_free(ctx);
    if (model) llama_model_free(model);
    ctx = nullptr;
    model = nullptr;
}

void throw_error(JNIEnv * env, const char * message) {
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
    const char * system = "Ты Chits, небольшой локальный ИИ-помощник. Отвечай по-русски кратко, ясно и честно. "
                          "Ты работаешь без интернета. Если не знаешь ответ, так и скажи. "
                          "Ты не можешь устанавливать приложения, менять настройки или загружать файлы сам. "
                          "В Chits Market скачанные APK устанавливаются со страницы Загрузки после подтверждения Android.";
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

std::string infer(const std::string & user, int max_tokens) {
    if (!model || !ctx) throw std::runtime_error("Load a GGUF model first");
    max_tokens = std::clamp(max_tokens, 16, 256);
    std::string prompt = chat_prompt(user);
    const llama_vocab * vocab = llama_model_get_vocab(model);
    int32_t count = -llama_tokenize(vocab, prompt.data(), static_cast<int32_t>(prompt.size()), nullptr, 0, true, true);
    if (count <= 0) throw std::runtime_error("Could not tokenize prompt");
    if (count + max_tokens + 4 > static_cast<int32_t>(llama_n_ctx(ctx)))
        throw std::runtime_error("Message is too long for the local model. Use a shorter question.");
    std::vector<llama_token> tokens(static_cast<size_t>(count));
    int32_t actual = llama_tokenize(vocab, prompt.data(), static_cast<int32_t>(prompt.size()), tokens.data(), count, true, true);
    if (actual <= 0) throw std::runtime_error("Could not tokenize prompt");
    tokens.resize(static_cast<size_t>(actual));
    llama_memory_clear(llama_get_memory(ctx), true);
    for (size_t offset = 0; offset < tokens.size(); offset += 512) {
        int32_t size = static_cast<int32_t>(std::min<size_t>(512, tokens.size() - offset));
        llama_batch batch = llama_batch_get_one(tokens.data() + offset, size);
        if (llama_decode(ctx, batch) != 0) throw std::runtime_error("Local model prompt evaluation failed");
    }
    llama_sampler * sampler = llama_sampler_chain_init(llama_sampler_chain_default_params());
    if (!sampler) throw std::runtime_error("Could not allocate sampler");
    llama_sampler_chain_add(sampler, llama_sampler_init_top_k(40));
    llama_sampler_chain_add(sampler, llama_sampler_init_top_p(0.9f, 1));
    llama_sampler_chain_add(sampler, llama_sampler_init_temp(0.65f));
    llama_sampler_chain_add(sampler, llama_sampler_init_dist(LLAMA_DEFAULT_SEED));
    std::string answer;
    try {
        for (int i = 0; i < max_tokens; ++i) {
            llama_token token = llama_sampler_sample(sampler, ctx, -1);
            if (llama_vocab_is_eog(vocab, token)) break;
            std::vector<char> piece(256);
            int32_t n = llama_token_to_piece(vocab, token, piece.data(), static_cast<int32_t>(piece.size()), 0, false);
            if (n < 0) {
                piece.resize(static_cast<size_t>(-n));
                n = llama_token_to_piece(vocab, token, piece.data(), static_cast<int32_t>(piece.size()), 0, false);
            }
            if (n < 0) throw std::runtime_error("Could not decode generated token");
            answer.append(piece.data(), static_cast<size_t>(n));
            if (i + 1 < max_tokens) {
                llama_batch batch = llama_batch_get_one(&token, 1);
                if (llama_decode(ctx, batch) != 0) throw std::runtime_error("Local model generation failed");
            }
        }
    } catch (...) {
        llama_sampler_free(sampler);
        throw;
    }
    llama_sampler_free(sampler);
    return answer;
}
}  // namespace

extern "C" JNIEXPORT jboolean JNICALL
Java_market_chits_OfflineAi_nativeLoad(JNIEnv * env, jclass, jstring path) {
    std::lock_guard<std::mutex> guard(state_mutex);
    if (!path) return JNI_FALSE;
    const char * chars = env->GetStringUTFChars(path, nullptr);
    if (!chars) return JNI_FALSE;
    std::string file(chars);
    env->ReleaseStringUTFChars(path, chars);
    try {
        cleanup();
        if (!backend_initialized) {
            // Do not log user prompts or local model metadata into Android logs.
            llama_log_set([](ggml_log_level, const char *, void *) {}, nullptr);
            llama_backend_init();
            backend_initialized = true;
        }
        auto mp = llama_model_default_params();
        mp.n_gpu_layers = 0;
        mp.use_mmap = true;
        model = llama_model_load_from_file(file.c_str(), mp);
        if (!model) throw std::runtime_error("Could not load GGUF model (invalid file or insufficient memory)");
        auto cp = llama_context_default_params();
        cp.n_ctx = 2048;
        cp.n_batch = 512;
        cp.n_ubatch = 128;
        cp.n_threads = 4;
        cp.n_threads_batch = 4;
        ctx = llama_init_from_model(model, cp);
        if (!ctx) throw std::runtime_error("Could not create model context (insufficient memory)");
        return JNI_TRUE;
    } catch (const std::exception & e) {
        cleanup();
        throw_error(env, e.what());
        return JNI_FALSE;
    }
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_market_chits_OfflineAi_nativeGenerate(JNIEnv * env, jclass, jbyteArray prompt, jint max_tokens) {
    std::lock_guard<std::mutex> guard(state_mutex);
    try {
        std::string answer = infer(byte_string(env, prompt), max_tokens);
        jbyteArray result = env->NewByteArray(static_cast<jsize>(answer.size()));
        if (!result) return nullptr;
        env->SetByteArrayRegion(result, 0, static_cast<jsize>(answer.size()), reinterpret_cast<const jbyte *>(answer.data()));
        return result;
    } catch (const std::exception & e) {
        throw_error(env, e.what());
        return nullptr;
    }
}

extern "C" JNIEXPORT void JNICALL
Java_market_chits_OfflineAi_nativeRelease(JNIEnv *, jclass) {
    std::lock_guard<std::mutex> guard(state_mutex);
    cleanup();
}
