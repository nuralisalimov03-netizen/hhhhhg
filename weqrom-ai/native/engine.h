#pragma once
#include "generation_control.h"
#include <llama.h>
#include <cstdint>
#include <string>
#include <vector>

namespace weqrom {
enum class FinishReason { Eos = 0, Length = 1, Cancelled = 2 };
// One caller owns the engine; cancellation alone is safe from another thread.
class Engine {
    llama_model * model_ = nullptr;
    llama_context * context_ = nullptr;
    bool backend_initialized_ = false;
    Cancellation cancellation_;
    FinishReason finish_reason_ = FinishReason::Cancelled;
public:
    ~Engine();
    void load(const std::string & path);
    void release();
    uint64_t epoch() const { return cancellation_.capture(); }
    void cancel() { cancellation_.cancel(); }
    bool cancelled() const { return cancellation_.requested(); }
    FinishReason finish_reason() const { return finish_reason_; }
    std::vector<llama_token> tokenize(const std::string & prompt) const;
    std::vector<float> next_logits(const std::string & prompt);
    std::string generate(const std::string & prompt, int max_tokens, uint64_t epoch,
                         TextStream::Callback callback = {});
};
}
