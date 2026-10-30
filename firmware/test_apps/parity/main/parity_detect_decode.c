#include <stdint.h>
#include <stdlib.h>

#include "detect/decode.h"
#include "parity.h"

enum { LEVELS = 3 };
enum { COL_BOX_EXPONENT = 0, COL_CLS_EXPONENT, COL_STRIDE, COLS };
enum { SCALE_X = 0, SCALE_Y, SCALES };
enum { PIC_BORDER_LEFT = 0, PIC_BORDER_TOP, PIC_WIDTH, PIC_HEIGHT, PIC_SCORE, PIC_KEEP, PICTURE };
enum { FIELD_X1 = 0, FIELD_Y1, FIELD_X2, FIELD_Y2, FIELD_CLASS, FIELD_SCORE, FIELDS };

static bool level_of(const gold_tensor_t *box, const gold_tensor_t *cls, const float *row,
                     ai_engine_detect_level_t *out)
{
    const bool narrow = box->dtype == GOLD_I8 && cls->dtype == GOLD_I8;
    const bool wide = box->dtype == GOLD_I16 && cls->dtype == GOLD_I16;
    if (!narrow && !wide) { return false; }
    *out = (ai_engine_detect_level_t){
        .box = box->data,
        .cls = cls->data,
        .box_exponent = (int8_t)row[COL_BOX_EXPONENT],
        .cls_exponent = (int8_t)row[COL_CLS_EXPONENT],
        .bytes = narrow ? 1 : 2,
        .grid = (uint16_t)box->dims[0],
        .stride = (uint16_t)row[COL_STRIDE],
    };
    return true;
}

bool parity_detect_decode(const char *case_name, const void *buf, size_t len)
{
    static const char *const box_names[LEVELS] = {"box0", "box1", "box2"};
    static const char *const cls_names[LEVELS] = {"cls0", "cls1", "cls2"};
    gold_tensor_t box[LEVELS], cls[LEVELS], exponents, frame, picture, want;
    float rows[LEVELS * COLS], scale[SCALES], pic[PICTURE];
    ai_engine_detect_level_t levels[LEVELS];
    for (int l = 0; l < LEVELS; l++) {
        if (!parity_tensor(buf, len, box_names[l], &box[l]) ||
            !parity_tensor(buf, len, cls_names[l], &cls[l])) {
            return false;
        }
    }
    if (!parity_tensor(buf, len, "exponents", &exponents) || !parity_tensor(buf, len, "frame", &frame) ||
        !parity_tensor(buf, len, "picture", &picture) || !parity_tensor(buf, len, "boxes", &want) ||
        !parity_floats(&exponents, rows, LEVELS * COLS) || !parity_floats(&frame, scale, SCALES) ||
        !parity_floats(&picture, pic, PICTURE) || want.dims[1] != FIELDS) {
        return false;
    }
    for (int l = 0; l < LEVELS; l++) {
        if (!level_of(&box[l], &cls[l], rows + l * COLS, &levels[l])) { return false; }
    }
    const ai_engine_detect_frame_t on = {
        .inverse_scale_x = scale[SCALE_X],
        .inverse_scale_y = scale[SCALE_Y],
        .border_left = (int16_t)pic[PIC_BORDER_LEFT],
        .border_top = (int16_t)pic[PIC_BORDER_TOP],
        .width = (uint16_t)pic[PIC_WIDTH],
        .height = (uint16_t)pic[PIC_HEIGHT],
    };
    const size_t k = (size_t)pic[PIC_KEEP];
    ai_engine_box_t *boxes = malloc(k * sizeof(*boxes));
    double *logits = malloc(k * sizeof(*logits));
    float *got = malloc(k * FIELDS * sizeof(*got));
    const bool ok = boxes != NULL && logits != NULL && got != NULL;
    if (ok) {
        const size_t n = ai_engine_detect_decode(levels, LEVELS, (uint16_t)cls[0].dims[2],
                                                 (uint16_t)pic[PIC_SCORE], &on, boxes, logits, k);
        const float counts[2] = {(float)want.dims[0], (float)n};
        parity_report("detect_decode", case_name, "count", &counts[0], &counts[1], 1);
        for (size_t i = 0; i < n; i++) {
            got[i * FIELDS + FIELD_X1] = boxes[i].x1;
            got[i * FIELDS + FIELD_Y1] = boxes[i].y1;
            got[i * FIELDS + FIELD_X2] = boxes[i].x2;
            got[i * FIELDS + FIELD_Y2] = boxes[i].y2;
            got[i * FIELDS + FIELD_CLASS] = boxes[i].cls;
            got[i * FIELDS + FIELD_SCORE] = boxes[i].score_permille;
        }
        const size_t both = n < want.dims[0] ? n : want.dims[0];
        parity_report("detect_decode", case_name, "boxes", want.data, got, both * FIELDS);
    }
    free(boxes);
    free(logits);
    free(got);
    return ok;
}
