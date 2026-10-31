#include <map>
#include <new>
#include <string.h>
#include <string>

#include "ai_engine.h"
#include "core/espdl_net.hpp"
#include "detect/decode.h"
#include "detect/detect.hpp"
#include "dl_image_define.hpp"
#include "dl_image_preprocessor.hpp"
#include "dl_model_base.hpp"
#include "esp_log.h"
#include "sha/sha_core.h"
#include "storage_format.h"
#include "sys_storage.h"

namespace {

const char *const TAG = "ai_detect";
constexpr uint8_t kPad = 114; // YOLO26's letterbox fill (KEHOACH 3.18)
constexpr size_t kLevels = 3;
constexpr size_t kRank = 4; // [1, grid, grid, channels], channels last
constexpr int kBoxSides = 4;
constexpr const char *kBoxes[kLevels] = {"one2one_p3_box", "one2one_p4_box", "one2one_p5_box"};
constexpr const char *kClasses[kLevels] = {"one2one_p3_cls", "one2one_p4_cls", "one2one_p5_cls"};
constexpr uint16_t kStrides[kLevels] = {8, 16, 32};

struct Detect {
    sys_storage_vision_t vision;
    dl::image::ImagePreprocessor *preprocessor;
    ai_engine_detect_level_t levels[kLevels];
    double logits[STORAGE_VISION_TOP_K_MAX]; // decode's scratch
};

ai::EspdlNet s_net;
Detect s;

dl::TensorBase *output(dl::Model *model, const char *name)
{
    std::map<std::string, dl::TensorBase *> &outputs = model->get_outputs();
    const auto found = outputs.find(name);
    return found != outputs.end() ? found->second : nullptr;
}

// A level's box and class outputs as the decode reads them; false when the graph lays them out otherwise.
bool level_of(dl::Model *model, size_t l, uint16_t classes, ai_engine_detect_level_t *out)
{
    dl::TensorBase *box = output(model, kBoxes[l]);
    dl::TensorBase *cls = output(model, kClasses[l]);
    if (box == nullptr || cls == nullptr || box->shape.size() != kRank || cls->shape.size() != kRank) {
        return false;
    }
    const bool narrow = box->get_dtype() == dl::DATA_TYPE_INT8 && cls->get_dtype() == dl::DATA_TYPE_INT8;
    const bool wide = box->get_dtype() == dl::DATA_TYPE_INT16 && cls->get_dtype() == dl::DATA_TYPE_INT16;
    const int grid = box->shape[1];
    if ((!narrow && !wide) || box->shape[2] != grid || box->shape[3] != kBoxSides || cls->shape[1] != grid ||
        cls->shape[2] != grid || cls->shape[3] != classes) {
        return false;
    }
    *out = {box->data,
            cls->data,
            static_cast<int8_t>(box->get_exponent()),
            static_cast<int8_t>(cls->get_exponent()),
            static_cast<uint8_t>(narrow ? 1 : 2),
            static_cast<uint16_t>(grid),
            kStrides[l]};
    return true;
}

void drop() noexcept
{
    delete s.preprocessor;
    s.preprocessor = nullptr;
    s_net.release();
    sys_storage_unmap_vision(&s.vision);
}

} // namespace

namespace ai {

dl::Model *detect_model() noexcept
{
    return s_net.model();
}

size_t detect_loan_bytes() noexcept
{
    return s_net.loan_bytes();
}

} // namespace ai

extern "C" esp_err_t ai_engine_detect_load(const char *label)
{
    if (s_net.ready()) { return ESP_ERR_INVALID_STATE; }
    esp_err_t err = sys_storage_map_vision(label, &s.vision);
    if (err != ESP_OK) { return err; }
    const storage_vision_header_t *head = s.vision.header;
    uint8_t digest[STORAGE_MODEL_SHA256_BYTES];
    esp_sha(SHA2_256, s.vision.espdl, head->espdl_size, digest);
    if (memcmp(digest, head->sha256, sizeof(digest)) != 0) {
        ESP_LOGE(TAG, "%s: the .espdl's sha256 differs from its header", label);
        drop();
        return ESP_ERR_INVALID_CRC;
    }
    err = s_net.build({s.vision.espdl, head->espdl_size}, "detect");
    if (err != ESP_OK) {
        drop();
        return err;
    }
    for (size_t l = 0; l < kLevels; l++) {
        if (!level_of(s_net.model(), l, head->classes, &s.levels[l])) {
            ESP_LOGE(TAG, "%s: %s and %s are not [1, g, g, 4] and [1, g, g, %u] of one type", label,
                     kBoxes[l], kClasses[l], (unsigned)head->classes);
            drop();
            return ESP_ERR_INVALID_SIZE;
        }
    }
    s.preprocessor =
        new (std::nothrow) dl::image::ImagePreprocessor(s_net.model(), {0, 0, 0}, {255, 255, 255});
    if (s.preprocessor == nullptr) {
        drop();
        return ESP_ERR_NO_MEM;
    }
    s.preprocessor->enable_letterbox({kPad, kPad, kPad});
    ESP_LOGI(TAG, "%.*s: %u classes at %u, top %u from %u permille", STORAGE_VISION_VERSION_BYTES,
             head->version, (unsigned)head->classes, (unsigned)head->size, (unsigned)head->top_k,
             (unsigned)head->score_permille);
    return ESP_OK;
}

extern "C" esp_err_t ai_engine_detect_run(const uint8_t *rgb, uint16_t width, uint16_t height,
                                          ai_engine_box_t *boxes, size_t cap, size_t *n)
{
    if (!s_net.ready()) { return ESP_ERR_INVALID_STATE; }
    if (rgb == nullptr || boxes == nullptr || n == nullptr || width == 0 || height == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    const dl::image::img_t img = {const_cast<uint8_t *>(rgb), width, height,
                                  dl::image::DL_IMAGE_PIX_TYPE_RGB888};
    s.preprocessor->preprocess(img);
    const esp_err_t err = s_net.step();
    if (err != ESP_OK) { return err; }
    const storage_vision_header_t *head = s.vision.header;
    const ai_engine_detect_frame_t frame = {
        s.preprocessor->get_resize_scale_x(true),
        s.preprocessor->get_resize_scale_y(true),
        static_cast<int16_t>(s.preprocessor->get_border_left()),
        static_cast<int16_t>(s.preprocessor->get_border_top()),
        width,
        height,
    };
    size_t k = cap < head->top_k ? cap : head->top_k;
    k = k < STORAGE_VISION_TOP_K_MAX ? k : STORAGE_VISION_TOP_K_MAX;
    *n = ai_engine_detect_decode(s.levels, kLevels, head->classes, head->score_permille, &frame, boxes,
                                 s.logits, k);
    return ESP_OK;
}

extern "C" const char *ai_engine_detect_name(uint16_t cls)
{
    return s_net.ready() && cls < s.vision.header->classes ? s.vision.header->names[cls] : nullptr;
}

extern "C" void ai_engine_detect_unload(void)
{
    drop();
}
