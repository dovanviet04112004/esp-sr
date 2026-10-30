/** YOLO26's one-to-one head decoded into boxes after the network, pure C and mirrored 1:1 by
 * srpipe.tasks.detect.postproc.decode (KEHOACH 3.18); contracts/golden/detect_decode/ holds it to the mirror.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "ai_engine.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    const void *box;     // [grid, grid, 4] left, top, right, bottom
    const void *cls;     // [grid, grid, classes] logits
    int8_t box_exponent; // value = q * 2^exponent
    int8_t cls_exponent;
    uint8_t bytes; // 1 for int8, 2 for int16, box and cls alike
    uint16_t grid; // cells a side
    uint16_t stride;
} ai_engine_detect_level_t;

typedef struct {
    float inverse_scale_x; // picture pixels a square pixel, as esp-dl's
    float inverse_scale_y;
    int16_t border_left; // letterbox fill left of the picture
    int16_t border_top;
    uint16_t width; // the picture's
    uint16_t height;
} ai_engine_detect_frame_t;

/** The least class logit, in steps of 2^exponent, whose sigmoid reaches score_permille, from double.
 *  @ctx any | non-blocking
 */
int32_t ai_engine_detect_floor(uint16_t score_permille, int exponent);

/** Each cell's best class whose logit reaches its level's floor, the k best cells by score, best first and
 * the first cell first on a tie, mapped back to the picture's pixels.
 *  @ctx any | non-blocking | caller owns every pointer; boxes and logits, its scratch, hold k each
 *  @ret boxes written, at most k
 */
size_t ai_engine_detect_decode(const ai_engine_detect_level_t *levels, size_t n_levels, uint16_t classes,
                               uint16_t score_permille, const ai_engine_detect_frame_t *frame,
                               ai_engine_box_t *boxes, double *logits, size_t k);

#ifdef __cplusplus
}
#endif
