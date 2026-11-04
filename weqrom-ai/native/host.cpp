#include "engine.h"
#include <fstream>
#include <iostream>
#include <iomanip>
#include <iterator>
#include <stdexcept>

int main(int argc, char ** argv) {
    try {
        std::string model, prompt_file;
        int count = 128;
        bool tokenize = false, logits = false;
        for (int index = 1; index < argc; ++index) {
            std::string option(argv[index]);
            if (option == "--tokenize") tokenize = true;
            else if (option == "--logits") logits = true;
            else if (index + 1 < argc && option == "--model") model = argv[++index];
            else if (index + 1 < argc && option == "--prompt-file") prompt_file = argv[++index];
            else if (index + 1 < argc && option == "--tokens") count = std::stoi(argv[++index]);
            else throw std::runtime_error("Usage: weqrom-ai-run --model FILE --prompt-file UTF8 [--tokens 128] [--tokenize|--logits]");
        }
        if (model.empty() || prompt_file.empty()) throw std::runtime_error("--model and --prompt-file are required");
        std::ifstream stream(prompt_file, std::ios::binary);
        if (!stream) throw std::runtime_error("Cannot read prompt file");
        std::string prompt((std::istreambuf_iterator<char>(stream)), std::istreambuf_iterator<char>());
        weqrom::Engine engine;
        engine.load(model);
        if (tokenize) {
            auto tokens = engine.tokenize(prompt);
            std::cout << '[';
            for (size_t index = 0; index < tokens.size(); ++index) std::cout << (index ? "," : "") << tokens[index];
            std::cout << "]\n";
        } else if (logits) {
            auto values = engine.next_logits(prompt);
            std::cout << '[' << std::setprecision(9);
            for (size_t index = 0; index < values.size(); ++index) std::cout << (index ? "," : "") << values[index];
            std::cout << "]\n";
        } else std::cout << engine.generate(prompt, count, engine.epoch()) << '\n';
        return 0;
    } catch (const std::exception & failure) { std::cerr << failure.what() << '\n'; return 1; }
}
