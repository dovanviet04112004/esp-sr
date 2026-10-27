#include "core/espdl/espdl_ops.hpp"

#include "core/espdl/espdl_conv_block.hpp"
#include "core/espdl/espdl_layernorm_s8.hpp"
#include "core/espdl/espdl_matmul_s8.hpp"
#include "core/espdl/espdl_softmax_quant.hpp"
#include "core/espdl/espdl_stage_sum.hpp"
#include "dl_module_creator.hpp"

namespace ai {

void register_espdl_ops() noexcept
{
    dl::module::ModuleCreator *creator = dl::module::ModuleCreator::get_instance();
    // esp-dl fills its own creators only into an empty map, so they go in first.
    creator->register_dl_modules();
    creator->register_module("SoftmaxQuant", softmax_quant_from);
    creator->register_module("LayerNormS8", layernorm_s8_from);
    creator->register_module("StageSum", stage_sum_from);
    creator->register_module("ConvBlock", conv_block_from);
    creator->register_module("MatMulS8", matmul_s8_from);
}

} // namespace ai
