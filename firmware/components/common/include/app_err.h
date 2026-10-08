/** Error codes the project adds to esp_err_t, and the codes events carry upward (contracts/schema/event).
 *  @ctx any | non-blocking
 */
#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define APP_ERR_BASE 0x70000
#define APP_ERR_GRID_MISMATCH (APP_ERR_BASE + 1)    // model trained on another grid
#define APP_ERR_CALIB_MISSING (APP_ERR_BASE + 2)    // NVS calib/* absent
#define APP_ERR_FRAME_GAP (APP_ERR_BASE + 3)        // seq jumped between two hops
#define APP_ERR_COMMANDS_INVALID (APP_ERR_BASE + 4) // not a command set, or lang_vi cannot read a line

#define APP_CODE_LOW_SCORE "LOW_SCORE"
#define APP_CODE_LOW_MARGIN "LOW_MARGIN"
#define APP_CODE_LOW_SYLLABLE "LOW_SYLLABLE" // the best command's worst syllable fell past delta3
#define APP_CODE_PART "PART"                 // a part of the best command scored at least as well
#define APP_CODE_TIMEOUT "TIMEOUT"
#define APP_CODE_FRAME_GAP "FRAME_GAP"
#define APP_CODE_GRID_MISMATCH "GRID_MISMATCH"
#define APP_CODE_COMMANDS_INVALID "COMMANDS_INVALID"     // with commandId: the line lang_vi cannot read
#define APP_CODE_COMMANDS_REFUSED "COMMANDS_REFUSED"     // this image cannot take a new set
#define APP_CODE_COMMANDS_NOT_SAVED "COMMANDS_NOT_SAVED" // in use until reboot, set.json old

#ifdef __cplusplus
}
#endif
