#include <stdbool.h>
#include <string.h>

#include "dsp_afe/ns.h"
#include "gen_grid.h"

// Neutral shell of E3-T4: every gain is 1 until E9-T1 writes OM-LSA and IMCRA (KEHOACH 3.9).

typedef struct {
    uint32_t frames;
} omlsa_state_t;

static bool config_ok(const dsp_afe_ns_omlsa_config_t *cfg)
{
    return cfg != NULL && cfg->floor_db <= 0.0f && cfg->noise_tau_s >= 0.0f;
}

static size_t state_bytes(void *ctx)
{
    return config_ok(ctx) ? sizeof(omlsa_state_t) : 0;
}

static esp_err_t init(void *ctx, void *state, size_t bytes)
{
    if (!config_ok(ctx) || state == NULL) { return ESP_ERR_INVALID_ARG; }
    if (bytes < sizeof(omlsa_state_t)) { return ESP_ERR_INVALID_SIZE; }
    memset(state, 0, sizeof(omlsa_state_t));
    return ESP_OK;
}

static esp_err_t process(void *ctx, void *state, const float *power, const float *echo_power, float *gain,
                         float *speech_prob)
{
    if (ctx == NULL || state == NULL || power == NULL || gain == NULL || speech_prob == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    (void)echo_power;
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        gain[k] = 1.0f;
    }
    *speech_prob = 0.0f;
    ((omlsa_state_t *)state)->frames++;
    return ESP_OK;
}

static const dsp_afe_ns_ops_t s_ops = {
    .state_bytes = state_bytes,
    .init = init,
    .process = process,
};

const dsp_afe_ns_ops_t *dsp_afe_ns_omlsa_ops(void)
{
    return &s_ops;
}
