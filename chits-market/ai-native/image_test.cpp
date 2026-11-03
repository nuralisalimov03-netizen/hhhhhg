#include <mtmd.h>
#include <mtmd-helper.h>
#include "image_resize.h"
#include <algorithm>
#include <iostream>
#include <stdexcept>
#include <vector>

static void check(bool condition, const char * message) {
    if (!condition) throw std::runtime_error(message);
}
int main() {
    try {
        const unsigned char png[] = {137,80,78,71,13,10,26,10,0,0,0,13,73,72,68,82,0,0,0,2,0,0,0,2,8,2,0,0,0,253,212,154,115,0,0,0,18,73,68,65,84,120,156,99,248,207,192,192,0,194,12,255,129,0,0,31,238,5,251,11,217,104,139,0,0,0,0,73,69,78,68,174,66,96,130};
        mtmd::bitmap_ptr decoded(mtmd_helper_bitmap_init_from_buf(nullptr, png, sizeof(png)));
        check(bool(decoded), "Real mtmd PNG decoder did not return an image");
        check(mtmd_bitmap_get_nx(decoded.get()) == 2 && mtmd_bitmap_get_ny(decoded.get()) == 2, "PNG dimensions differ");
        const unsigned char expected[] = {255,0,0,0,255,0,0,0,255,255,255,255};
        check(mtmd_bitmap_get_n_bytes(decoded.get()) == sizeof(expected), "PNG RGB buffer length differs");
        check(std::equal(expected, expected + sizeof(expected), mtmd_bitmap_get_data(decoded.get())), "PNG pixels differ");
        auto identity = chits::resize_rgb(mtmd_bitmap_get_data(decoded.get()), 2, 2);
        check(identity.pixels == std::vector<unsigned char>(expected, expected + sizeof(expected)), "Small photo changed unexpectedly");
        auto average = chits::resize_rgb(mtmd_bitmap_get_data(decoded.get()), 2, 2, 1);
        check(average.width == 1 && average.height == 1 && average.pixels == std::vector<unsigned char>({128,128,128}), "Resized photo lost expected color interpolation");
        std::vector<unsigned char> large(1120 * 560 * 3, 37);
        auto limited = chits::resize_rgb(large.data(), 1120, 560);
        check(limited.width == 560 && limited.height == 280, "Phone memory photo bound or aspect ratio failed");
        check(limited.pixels.size() == 560 * 280 * 3 && std::all_of(limited.pixels.begin(), limited.pixels.end(), [](auto c){ return c == 37; }), "Constant image resize corrupted pixels");
        std::vector<unsigned char> portrait(1024 * 3, 1);
        auto thin = chits::resize_rgb(portrait.data(), 1, 1024);
        check(thin.width == 1 && thin.height == 560, "Thin photo dimensions became invalid");
        bool rejected = false;
        try { chits::resize_rgb(nullptr, 1, 1); } catch (const std::runtime_error &) { rejected = true; }
        check(rejected, "Null RGB buffer accepted");
        rejected = false;
        try { chits::resize_rgb(large.data(), 5000, 1); } catch (const std::runtime_error &) { rejected = true; }
        check(rejected, "Oversized image dimensions accepted");
        const unsigned char invalid[] = {0,1,2,3};
        mtmd::bitmap_ptr invalid_image(mtmd_helper_bitmap_init_from_buf(nullptr, invalid, sizeof(invalid)));
        check(!invalid_image, "Broken image bytes accepted");
        std::cout << "PASS: actual mtmd PNG decoding, RGB values, 560px memory cap, aspect ratio, bilinear resizing, invalid image handling\n";
        return 0;
    } catch (const std::exception & error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
