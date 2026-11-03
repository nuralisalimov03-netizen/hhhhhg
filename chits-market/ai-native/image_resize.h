#ifndef CHITS_IMAGE_RESIZE_H
#define CHITS_IMAGE_RESIZE_H
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace chits {
struct RgbImage {
    uint32_t width;
    uint32_t height;
    std::vector<unsigned char> pixels;
};
// A 560px long edge bounds the Qwen vision attention graph on an 8GB phone.
inline RgbImage resize_rgb(const unsigned char * data, uint32_t width, uint32_t height, uint32_t max_edge = 560) {
    if (!data || width == 0 || height == 0 || width > 4096 || height > 4096 || max_edge == 0 || max_edge > 1024)
        throw std::runtime_error("Unsupported image dimensions");
    const double scale = std::min(1.0, double(max_edge) / std::max(width, height));
    RgbImage out{
        std::max(1u, static_cast<uint32_t>(std::lround(width * scale))),
        std::max(1u, static_cast<uint32_t>(std::lround(height * scale))),
        {}
    };
    out.pixels.resize(static_cast<size_t>(out.width) * out.height * 3);
    if (out.width == width && out.height == height) {
        std::copy(data, data + out.pixels.size(), out.pixels.begin());
        return out;
    }
    for (uint32_t y = 0; y < out.height; ++y) {
        double sy = std::max(0.0, (y + 0.5) * height / out.height - 0.5);
        uint32_t y0 = std::min(height - 1, static_cast<uint32_t>(sy));
        uint32_t y1 = std::min(height - 1, y0 + 1);
        double fy = sy - y0;
        for (uint32_t x = 0; x < out.width; ++x) {
            double sx = std::max(0.0, (x + 0.5) * width / out.width - 0.5);
            uint32_t x0 = std::min(width - 1, static_cast<uint32_t>(sx));
            uint32_t x1 = std::min(width - 1, x0 + 1);
            double fx = sx - x0;
            for (uint32_t c = 0; c < 3; ++c) {
                double top = data[(static_cast<size_t>(y0) * width + x0) * 3 + c] * (1 - fx) +
                             data[(static_cast<size_t>(y0) * width + x1) * 3 + c] * fx;
                double bottom = data[(static_cast<size_t>(y1) * width + x0) * 3 + c] * (1 - fx) +
                                data[(static_cast<size_t>(y1) * width + x1) * 3 + c] * fx;
                out.pixels[(static_cast<size_t>(y) * out.width + x) * 3 + c] = static_cast<unsigned char>(std::lround(top * (1 - fy) + bottom * fy));
            }
        }
    }
    return out;
}
}
#endif
