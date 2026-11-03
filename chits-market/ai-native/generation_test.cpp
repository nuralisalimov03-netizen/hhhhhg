#include "generation_control.h"
#include <ggml.h>
#include <ggml-cpu.h>
#include <algorithm>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <vector>

static void check(bool value, const char * message) {
    if (!value) throw std::runtime_error(message);
}
int main() {
    try {
        using Clock = chits::TextStream::Clock;
        const auto start = Clock::time_point{};
        std::vector<std::string> updates;
        chits::TextStream text([&](const std::string & value){ updates.push_back(value); });
        const std::string response = "Привет 🌍!";
        // Simulate token boundaries inside both Cyrillic and a four-byte emoji.
        text.append(response.data(), 1, start);
        check(updates.empty(), "Incomplete Cyrillic codepoint leaked to listener");
        text.append(response.data() + 1, 1, start);
        check(updates.size() == 1 && updates.back() == "П", "First complete text was not delivered immediately");
        text.append(response.data() + 2, 11, start + std::chrono::milliseconds(50));
        check(updates.size() == 1, "Small fast update was not throttled");
        text.append(response.data() + 13, 1, start + std::chrono::milliseconds(110));
        check(updates.size() == 2 && updates.back() == "Привет ", "Partial emoji leaked or timed update was omitted");
        text.append(response.data() + 14, response.size() - 14, start + std::chrono::milliseconds(120));
        check(text.finish() == response && updates.back() == response, "Final UTF-8 output differs");
        std::vector<std::string> threshold;
        chits::TextStream bytes([&](const std::string & value){ threshold.push_back(value); });
        bytes.append("a", 1, start);
        bytes.append("1234567890123456", 16, start + std::chrono::milliseconds(1));
        check(threshold.size() == 2, "16-byte progress threshold did not emit");
        chits::TextStream cancelled_text;
        cancelled_text.append("ok\xf0\x9f", 4);
        check(cancelled_text.finish() == "ok", "Cancellation left a partial UTF-8 character");

        // Exercise the actual ggml CPU abort hook used by llama_decode, without
        // claiming that this synthetic graph is language-model inference.
        ggml_context * ctx = ggml_init({128 * 1024 * 1024, nullptr, false});
        check(ctx != nullptr, "Could not allocate CPU test graph");
        ggml_tensor * input = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, 256, 256);
        ggml_set_f32(input, 0.0001f);
        ggml_tensor * output = input;
        for (int i = 0; i < 64; ++i) output = ggml_mul_mat(ctx, input, output);
        ggml_cgraph * graph = ggml_new_graph(ctx);
        ggml_build_forward_expand(graph, output);
        ggml_cplan plan = ggml_graph_plan(graph, 2, nullptr);
        std::vector<uint8_t> work(plan.work_size);
        plan.work_data = work.data();
        chits::Cancellation cancellation;
        const uint64_t queued_epoch = cancellation.capture();
        cancellation.cancel();
        cancellation.begin(queued_epoch);
        check(cancellation.requested(), "Queued generation lost cancellation before its native entry");
        cancellation.reset();
        plan.abort_callback = [](void * data){ return static_cast<chits::Cancellation *>(data)->requested(); };
        plan.abort_callback_data = &cancellation;
        std::thread stop([&] { std::this_thread::sleep_for(std::chrono::milliseconds(1)); cancellation.cancel(); });
        ggml_status status = ggml_graph_compute(graph, &plan);
        stop.join();
        check(status == GGML_STATUS_ABORTED, "Real ggml CPU graph did not abort on cancellation");
        cancellation.reset();
        check(!cancellation.requested(), "Next generation retained stale cancellation");
        ggml_free(ctx);
        std::cout << "PASS: streamed UTF-8 boundaries/throttling/final prefix; actual ggml CPU cancellation\n";
        return 0;
    } catch (const std::exception & error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
