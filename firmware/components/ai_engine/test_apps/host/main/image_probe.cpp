#include "image_probe.h"

#include "core/model_image.hpp"

extern "C" const uint8_t *image_probe_find(const char *name, uint32_t kind, size_t *size)
{
    const ai::Blob blob = ai::image_find(name, static_cast<storage_model_kind_t>(kind));
    *size = blob.size;
    return blob.data;
}

extern "C" size_t image_probe_bytes(void)
{
    return ai::image_bytes();
}

extern "C" bool image_probe_listens(const char *name)
{
    return ai::image_listens_as_built(name);
}

extern "C" bool image_probe_speaks(const char *name)
{
    return ai::image_speaks_as_built(name);
}

extern "C" int16_t image_probe_speaker_threshold(void)
{
    return ai::image_speaker_threshold_permille();
}

extern "C" uint32_t image_probe_id(const char *name, uint32_t kind)
{
    return ai::image_entry_id(name, static_cast<storage_model_kind_t>(kind));
}
