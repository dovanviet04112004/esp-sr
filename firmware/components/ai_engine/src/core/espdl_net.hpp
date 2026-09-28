/** One esp-dl network over an entry of the loaded image (KEHOACH 6.5): graph, tensors and streaming caches in
 * PSRAM, built once at load, then one run per hop. Knows no model by name.
 *  @ctx task | build allocates and blocks, at boot only; step and reset belong to the task that owns the
 * branch
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "core/model_image.hpp"
#include "esp_err.h"

namespace dl {
class Model;
} // namespace dl

namespace ai {

struct Int8Tensor {
    int8_t *data; // value = data[i] * 2^exponent; nullptr if unbuilt
    size_t elements;
    int exponent;
};

class EspdlNet {
  public:
    EspdlNet() = default;
    EspdlNet(const EspdlNet &) = delete;
    EspdlNet &operator=(const EspdlNet &) = delete;

    /** Build the graph over blob, which must stay loaded, then run one step and reset, so every streaming
     * cache is allocated here and not on the first hop.
     *  @ret ESP_OK | ESP_ERR_INVALID_STATE built already | ESP_ERR_NO_MEM | ESP_ERR_NOT_SUPPORTED esp-dl
     * stopped
     */
    esp_err_t build(Blob blob, const char *name) noexcept;
    Int8Tensor input() noexcept;
    Int8Tensor output() noexcept;
    /** One run over what input() holds; streaming caches keep the earlier hops. */
    esp_err_t step() noexcept;
    /** Clear every streaming cache, as after a gap in the frame sequence. */
    void reset() noexcept;
    bool ready() const noexcept
    {
        return model_ != nullptr;
    }

  private:
    dl::Model *model_ = nullptr;
    const char *name_ = "";
};

} // namespace ai
