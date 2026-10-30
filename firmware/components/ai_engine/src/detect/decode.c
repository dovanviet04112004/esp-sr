#include "detect/decode.h"

#include <math.h>

#define BOX_SIDES 4
#define PERMILLE 1000

static int32_t value_at(const void *data, uint8_t bytes, size_t i)
{
    return bytes == 1 ? ((const int8_t *)data)[i] : ((const int16_t *)data)[i];
}

int32_t ai_engine_detect_floor(uint16_t score_permille, int exponent)
{
    const double p = score_permille / (double)PERMILLE;
    const double q = ceil(log(p / (1.0 - p)) / ldexp(1.0, exponent));
    return q < INT32_MIN ? INT32_MIN : (q > INT32_MAX ? INT32_MAX : (int32_t)q);
}

static int16_t on_picture(int square, int border, float inverse_scale, uint16_t extent)
{
    const int v = (int)((float)(square - border) * inverse_scale);
    return (int16_t)(v < 0 ? 0 : (v > extent - 1 ? extent - 1 : v));
}

// Best first by the exact logit q * 2^e; ties keep scan order, a later cell after an equal one.
static size_t keep(double logit, const ai_engine_box_t *box, double *logits, ai_engine_box_t *boxes, size_t n,
                   size_t k)
{
    if (n == k && logit <= logits[n - 1]) { return n; }
    size_t at = n < k ? n : k - 1;
    while (at > 0 && logits[at - 1] < logit) {
        logits[at] = logits[at - 1];
        boxes[at] = boxes[at - 1];
        at--;
    }
    logits[at] = logit;
    boxes[at] = *box;
    return n < k ? n + 1 : n;
}

size_t ai_engine_detect_decode(const ai_engine_detect_level_t *levels, size_t n_levels, uint16_t classes,
                               uint16_t score_permille, const ai_engine_detect_frame_t *frame,
                               ai_engine_box_t *boxes, double *logits, size_t k)
{
    if (k == 0 || classes == 0) { return 0; }
    size_t n = 0;
    for (size_t l = 0; l < n_levels; l++) {
        const ai_engine_detect_level_t *lv = &levels[l];
        const int32_t floor_q = ai_engine_detect_floor(score_permille, lv->cls_exponent);
        const float box_step = ldexpf(1.0f, lv->box_exponent);
        for (uint16_t h = 0; h < lv->grid; h++) {
            for (uint16_t w = 0; w < lv->grid; w++) {
                const size_t cell = (size_t)h * lv->grid + w;
                int32_t best_q = value_at(lv->cls, lv->bytes, cell * classes);
                uint16_t best = 0;
                for (uint16_t c = 1; c < classes; c++) {
                    const int32_t q = value_at(lv->cls, lv->bytes, cell * classes + c);
                    if (q > best_q) {
                        best_q = q;
                        best = c;
                    }
                }
                if (best_q < floor_q) { continue; }
                const float cx = (float)w + 0.5f;
                const float cy = (float)h + 0.5f;
                const float step = (float)lv->stride;
                float side[BOX_SIDES];
                for (int s = 0; s < BOX_SIDES; s++) {
                    side[s] = (float)value_at(lv->box, lv->bytes, cell * BOX_SIDES + s) * box_step;
                }
                const ai_engine_box_t box = {
                    .x1 = on_picture((int)((cx - side[0]) * step), frame->border_left, frame->inverse_scale_x,
                                     frame->width),
                    .y1 = on_picture((int)((cy - side[1]) * step), frame->border_top, frame->inverse_scale_y,
                                     frame->height),
                    .x2 = on_picture((int)((cx + side[2]) * step), frame->border_left, frame->inverse_scale_x,
                                     frame->width),
                    .y2 = on_picture((int)((cy + side[3]) * step), frame->border_top, frame->inverse_scale_y,
                                     frame->height),
                    .cls = best,
                    .score_permille = 0,
                };
                n = keep(ldexp((double)best_q, lv->cls_exponent), &box, logits, boxes, n, k);
            }
        }
    }
    for (size_t i = 0; i < n; i++) {
        const float score = (float)(1.0 / (1.0 + exp(-logits[i])));
        boxes[i].score_permille = (uint16_t)(score * (float)PERMILLE + 0.5f);
    }
    return n;
}
