#include "engine.h"
#include <algorithm>
#include <memory>
#include <stdexcept>
#include <thread>

namespace weqrom {
Engine::~Engine() { release(); }

void Engine::release() {
    if (context_) llama_free(context_);
    if (model_) llama_model_free(model_);
    context_ = nullptr;
    model_ = nullptr;
}

void Engine::load(const std::string & path) {
    release();
    cancellation_.reset();
    if (!backend_initialized_) {
        llama_log_set([](ggml_log_level, const char *, void *) {}, nullptr);
        llama_backend_init();
        backend_initialized_ = true;
    }
    try {
        auto model_params = llama_model_default_params();
        model_params.n_gpu_layers = 0;
        model_params.use_mmap = true;
        model_params.progress_callback = [](float, void * data) {
            return !static_cast<Engine *>(data)->cancelled();
        };
        model_params.progress_callback_user_data = this;
        model_ = llama_model_load_from_file(path.c_str(), model_params);
        if (!model_) throw std::runtime_error("Не удалось загрузить встроенную модель GGUF.");
        // This application trains its own ChatML delimiters. Stop metadata must
        // name that exact delimiter, rather than an unrelated inherited EOS.
        auto end = tokenize("<|im_end|>");
        if (end.size() != 1 || !llama_vocab_is_eog(llama_model_get_vocab(model_), end[0]))
            throw std::runtime_error("В модели отсутствует корректный токен конца ответа.");
        auto params = llama_context_default_params();
        params.n_ctx = 2048;
        params.n_batch = 256;
        params.n_ubatch = 128;
        unsigned cores = std::thread::hardware_concurrency();
        params.n_threads = static_cast<int>(std::max(1u, std::min(4u, cores ? cores : 2u)));
        params.n_threads_batch = params.n_threads;
        params.no_perf = true;
        params.abort_callback = [](void * data) { return static_cast<Engine *>(data)->cancelled(); };
        params.abort_callback_data = this;
        context_ = llama_init_from_model(model_, params);
        if (!context_) throw std::runtime_error("Недостаточно памяти для контекста модели.");
    } catch (...) {
        release();
        throw;
    }
}

std::vector<llama_token> Engine::tokenize(const std::string & prompt) const {
    if (!model_) throw std::runtime_error("Модель ещё не загружена.");
    if (prompt.size() > 32768) throw std::runtime_error("Запрос превышает 32 КиБ UTF-8.");
    const llama_vocab * vocab = llama_model_get_vocab(model_);
    // Training uses no leading BOS, and ChatML control tokens are intentional.
    int count = -llama_tokenize(vocab, prompt.data(), static_cast<int32_t>(prompt.size()), nullptr, 0, false, true);
    if (count <= 0) throw std::runtime_error("Не удалось разобрать запрос на токены.");
    std::vector<llama_token> result(static_cast<size_t>(count));
    int actual = llama_tokenize(vocab, prompt.data(), static_cast<int32_t>(prompt.size()), result.data(), count, false, true);
    if (actual <= 0) throw std::runtime_error("Не удалось разобрать запрос на токены.");
    result.resize(static_cast<size_t>(actual));
    return result;
}

std::vector<float> Engine::next_logits(const std::string & prompt) {
    if (!context_) throw std::runtime_error("Model is not loaded");
    cancellation_.reset();
    auto tokens = tokenize(prompt);
    if (tokens.size() > llama_n_ctx(context_)) throw std::runtime_error("Prompt exceeds context");
    llama_memory_clear(llama_get_memory(context_), true);
    for (size_t offset = 0; offset < tokens.size(); offset += 256) {
        int count = static_cast<int>(std::min<size_t>(256, tokens.size() - offset));
        auto batch = llama_batch_get_one(tokens.data() + offset, count);
        if (llama_decode(context_, batch) != 0) throw std::runtime_error("Prompt evaluation failed");
    }
    auto logits = llama_get_logits_ith(context_, -1);
    if (!logits) throw std::runtime_error("No model logits");
    return std::vector<float>(logits, logits + llama_vocab_n_tokens(llama_model_get_vocab(model_)));
}

std::string Engine::generate(const std::string & prompt, int max_tokens, uint64_t epoch,
                             TextStream::Callback callback) {
    if (!context_) throw std::runtime_error("Модель ещё не готова.");
    cancellation_.begin(epoch);
    if (cancelled()) return {};
    max_tokens = std::clamp(max_tokens, 1, 512);
    auto tokens = tokenize(prompt);
    if (tokens.size() + static_cast<size_t>(max_tokens) > llama_n_ctx(context_))
        throw std::runtime_error("История и ответ превышают контекст 2048 токенов. Начните новый диалог или сократите запрос.");
    llama_memory_clear(llama_get_memory(context_), true);
    for (size_t offset = 0; offset < tokens.size(); offset += 256) {
        if (cancelled()) return {};
        int count = static_cast<int>(std::min<size_t>(256, tokens.size() - offset));
        auto batch = llama_batch_get_one(tokens.data() + offset, count);
        int status = llama_decode(context_, batch);
        if (cancelled()) return {};
        if (status != 0) throw std::runtime_error("Не удалось обработать запрос локальной моделью.");
    }
    using Sampler = std::unique_ptr<llama_sampler, decltype(&llama_sampler_free)>;
    Sampler sampler(llama_sampler_init_greedy(), llama_sampler_free);
    if (!sampler) throw std::runtime_error("Не удалось создать генератор токенов.");
    TextStream stream(std::move(callback));
    auto vocab = llama_model_get_vocab(model_);
    for (int index = 0; index < max_tokens; ++index) {
        if (cancelled()) break;
        llama_token token = llama_sampler_sample(sampler.get(), context_, -1);
        if (llama_vocab_is_eog(vocab, token)) break;
        std::vector<char> piece(128);
        int length = llama_token_to_piece(vocab, token, piece.data(), static_cast<int>(piece.size()), 0, false);
        if (length < 0) {
            piece.resize(static_cast<size_t>(-length));
            length = llama_token_to_piece(vocab, token, piece.data(), static_cast<int>(piece.size()), 0, false);
        }
        if (length < 0) throw std::runtime_error("Не удалось прочитать токен ответа.");
        stream.append(piece.data(), static_cast<size_t>(length));
        if (cancelled() || index + 1 == max_tokens) break;
        auto batch = llama_batch_get_one(&token, 1);
        int status = llama_decode(context_, batch);
        if (cancelled()) break;
        if (status != 0) throw std::runtime_error("Ошибка локальной генерации ответа.");
    }
    return stream.finish();
}
}
