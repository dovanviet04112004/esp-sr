#include "core/espdl_net.hpp"

#include <new>

#include "dl_model_base.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "sdkconfig.h"

namespace ai {

namespace {

const char *const TAG = "ai_net";

// A graph's small mallocs would go internal below SPIRAM_MALLOC_ALWAYSINTERNAL (KEHOACH 6.5).
class ExternalMallocGuard {
  public:
    ExternalMallocGuard() noexcept
    {
        heap_caps_malloc_extmem_enable(0);
    }
    ~ExternalMallocGuard()
    {
        heap_caps_malloc_extmem_enable(CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL);
    }
    ExternalMallocGuard(const ExternalMallocGuard &) = delete;
    ExternalMallocGuard &operator=(const ExternalMallocGuard &) = delete;
};

Int8Tensor view(dl::TensorBase *tensor) noexcept
{
    if (tensor == nullptr || tensor->get_dtype() != dl::DATA_TYPE_INT8) { return {nullptr, 0, 0}; }
    return {tensor->get_element_ptr<int8_t>(), static_cast<size_t>(tensor->get_size()),
            tensor->get_exponent()};
}

} // namespace

esp_err_t EspdlNet::build(Blob blob, const char *name) noexcept
{
    if (model_ != nullptr) { return ESP_ERR_INVALID_STATE; }
    if (blob.data == nullptr || name == nullptr) { return ESP_ERR_INVALID_ARG; }
    name_ = name;
    const size_t internal_before = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    const size_t psram_before = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    const int64_t started_us = esp_timer_get_time();
    dl::Model *model = nullptr;
    {
        const ExternalMallocGuard guard;
        // Every tensor in PSRAM; the parameters stay in the loaded copy, itself in PSRAM.
        model = new (std::nothrow)
            dl::Model(reinterpret_cast<const char *>(blob.data), fbs::MODEL_LOCATION_IN_FLASH_RODATA, 0,
                      dl::MEMORY_MANAGER_GREEDY, nullptr, false);
        if (model != nullptr && !model->get_inputs().empty() && !model->get_outputs().empty()) {
            // StreamingCache allocates its cache on its first run, which must happen at boot (CLAUDE.md 4.1).
            model->run();
            model->reset();
        }
    }
    if (model == nullptr) { return ESP_ERR_NO_MEM; }
    if (model->get_inputs().size() != 1 || model->get_outputs().size() != 1) {
        ESP_LOGE(TAG, "%s: esp-dl built %u inputs and %u outputs, want one of each, see its log above", name_,
                 (unsigned)model->get_inputs().size(), (unsigned)model->get_outputs().size());
        delete model;
        return ESP_ERR_NOT_SUPPORTED;
    }
    model_ = model;
    ESP_LOGI(TAG, "%s: built in %lld us, internal %u B, psram %u B", name_,
             static_cast<long long>(esp_timer_get_time() - started_us),
             (unsigned)(internal_before - heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
             (unsigned)(psram_before - heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
    return ESP_OK;
}

Int8Tensor EspdlNet::input() noexcept
{
    return model_ != nullptr ? view(model_->get_inputs().begin()->second) : Int8Tensor{nullptr, 0, 0};
}

Int8Tensor EspdlNet::output() noexcept
{
    return model_ != nullptr ? view(model_->get_outputs().begin()->second) : Int8Tensor{nullptr, 0, 0};
}

esp_err_t EspdlNet::step() noexcept
{
    if (model_ == nullptr) { return ESP_ERR_INVALID_STATE; }
    model_->run();
    return ESP_OK;
}

void EspdlNet::reset() noexcept
{
    if (model_ != nullptr) { model_->reset(); }
}

} // namespace ai
