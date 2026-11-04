#ifndef WEQROM_GENERATION_CONTROL_H
#define WEQROM_GENERATION_CONTROL_H
#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <string>

namespace weqrom {
// Generation owns this flag; cancellation never waits for the inference mutex.
class Cancellation {
    std::atomic<uint64_t> cancelled_epoch_{0};
    std::atomic<uint64_t> active_epoch_{0};
public:
    uint64_t capture() const { return cancelled_epoch_.load(std::memory_order_acquire); }
    void begin(uint64_t epoch) { active_epoch_.store(epoch, std::memory_order_release); }
    void reset() { begin(capture()); }
    void cancel() { cancelled_epoch_.fetch_add(1, std::memory_order_acq_rel); }
    bool requested() const { return capture() != active_epoch_.load(std::memory_order_acquire); }
};

// Sentence pieces may split a multibyte UTF-8 codepoint across model tokens.
// Hold only an incomplete trailing sequence; Java replaces malformed complete data.
inline size_t complete_utf8_prefix_size(const std::string & text) {
    if (text.empty()) return 0;
    size_t start = text.size() - 1;
    while (start > 0 && (static_cast<unsigned char>(text[start]) & 0xc0) == 0x80) --start;
    unsigned char lead = static_cast<unsigned char>(text[start]);
    size_t expected = lead >= 0xc2 && lead <= 0xdf ? 2 : lead >= 0xe0 && lead <= 0xef ? 3 : lead >= 0xf0 && lead <= 0xf4 ? 4 : 1;
    return text.size() - start < expected ? start : text.size();
}

class TextStream {
public:
    using Clock = std::chrono::steady_clock;
    using Callback = std::function<void(const std::string &)>;
private:
    std::string bytes_;
    Callback callback_;
    size_t emitted_ = 0;
    Clock::time_point last_{};
public:
    explicit TextStream(Callback callback = {}) : callback_(std::move(callback)) {}
    void append(const char * piece, size_t length, Clock::time_point now = Clock::now()) {
        bytes_.append(piece, length);
        size_t complete = complete_utf8_prefix_size(bytes_);
        if (!callback_ || complete == emitted_ || complete == 0) return;
        if (emitted_ == 0 || complete - emitted_ >= 16 || now - last_ >= std::chrono::milliseconds(100)) {
            callback_(bytes_.substr(0, complete));
            emitted_ = complete;
            last_ = now;
        }
    }
    std::string finish() {
        size_t complete = complete_utf8_prefix_size(bytes_);
        std::string answer = bytes_.substr(0, complete);
        if (callback_ && complete != emitted_) {
            callback_(answer);
            emitted_ = complete;
        }
        return answer;
    }
};
}
#endif
