#include "core/espdl/espdl_ops.hpp"

#include "core/espdl/ops/espdl_attention.hpp"
#include "core/espdl/ops/espdl_band_block.hpp"
#include "core/espdl/ops/espdl_band_chain.hpp"
#include "core/espdl/ops/espdl_band_head.hpp"
#include "core/espdl/ops/espdl_band_stem.hpp"
#include "core/espdl/ops/espdl_band_tail.hpp"
#include "core/espdl/ops/espdl_conv_block.hpp"
#include "core/espdl/ops/espdl_global_stats.hpp"
#include "core/espdl/ops/espdl_group_norm_s8.hpp"
#include "core/espdl/ops/espdl_layernorm_s8.hpp"
#include "core/espdl/ops/espdl_matmul_s8.hpp"
#include "core/espdl/ops/espdl_pointwise_s8.hpp"
#include "core/espdl/ops/espdl_softmax_quant.hpp"
#include "core/espdl/ops/espdl_stage_sum.hpp"
#include "core/espdl/ops/espdl_stem_conv.hpp"
#include "core/espdl/ops/espdl_stride_depthwise.hpp"
#include "core/espdl/ops/espdl_time_depthwise.hpp"
#include "core/espdl/ops/espdl_weighted_stats.hpp"
#include "dl_module_creator.hpp"

namespace ai {

void register_espdl_ops() noexcept
{
    dl::module::ModuleCreator *creator = dl::module::ModuleCreator::get_instance();
    // esp-dl fills its own creators only into an empty map, so they go in first.
    creator->register_dl_modules();
    creator->register_module("SoftmaxQuant", softmax_quant_from);
    creator->register_module("LayerNormS8", layernorm_s8_from);
    creator->register_module("GroupNormS8", group_norm_s8_from);
    creator->register_module("StageSum", stage_sum_from);
    creator->register_module("ConvBlock", conv_block_from);
    creator->register_module("MatMulS8", matmul_s8_from);
    creator->register_module("AttentionCore", attention_core_from);
    creator->register_module("StemConv", stem_conv_from);
    creator->register_module("StrideDepthwise", stride_depthwise_from);
    creator->register_module("TimeDepthwise", time_depthwise_from);
    creator->register_module("WeightedStats", weighted_stats_from);
    creator->register_module("GlobalStats", global_stats_from);
    creator->register_module("PointwiseS8", pointwise_s8_from);
    creator->register_module("BandBlock", band_block_from);
    creator->register_module("BandHead", band_head_from);
    creator->register_module("BandStem", band_stem_from);
    creator->register_module("BandChain", band_chain_from);
    creator->register_module("BandTail", band_tail_from);
}

} // namespace ai
