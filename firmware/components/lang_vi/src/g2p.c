#include <stdbool.h>

#include "lang_vi.h"

// Neutral shell of E3-T4: no unit comes out until E11-T3 fixes the inventory and E11-T4 the rules.

#define UNKNOWN_UNIT_NAME "?"

static bool one_dialect(lang_vi_dialect_t dialect)
{
    return dialect == LANG_VI_DIALECT_NORTH || dialect == LANG_VI_DIALECT_CENTRAL ||
           dialect == LANG_VI_DIALECT_SOUTH;
}

esp_err_t lang_vi_g2p(const char *normalized, lang_vi_dialect_t dialect, lang_vi_unit_t *units, size_t cap,
                      size_t *n_units)
{
    if (normalized == NULL || n_units == NULL || (units == NULL && cap > 0) || !one_dialect(dialect)) {
        return ESP_ERR_INVALID_ARG;
    }
    *n_units = 0;
    return ESP_OK;
}

const char *lang_vi_unit_name(lang_vi_unit_t unit)
{
    (void)unit;
    return UNKNOWN_UNIT_NAME;
}
