# The probes are exported by ml, never committed: make ai-probe writes them here.
foreach(probe models.bin stream.bin kws_models.bin kws_windows.bin ctc_models.bin ctc_streams.bin ctc_decide.bin
              ns_models.bin ns_streams.bin)
    if(NOT EXISTS "${CMAKE_CURRENT_LIST_DIR}/probe/${probe}")
        message(FATAL_ERROR "main/probe/${probe} is missing: run make ai-probe")
    endif()
endforeach()

idf_component_register(
    SRCS "test_ai_engine.c" "test_stream.cpp" "test_kws_probe.cpp" "test_ctc_probe.cpp" "test_ns_probe.cpp"
         "test_ctc_decide.c" "../../../src/command_ctc/ctc_score.c"
    PRIV_INCLUDE_DIRS "../../../src"
    REQUIRES ai_engine sys_storage unity esp_partition esp_timer mbedtls espressif__esp-dl
    EMBED_FILES "probe/models.bin" "probe/stream.bin" "probe/kws_models.bin" "probe/kws_windows.bin"
                "probe/ctc_streams.bin" "probe/ctc_decide.bin" "probe/ns_streams.bin"
    WHOLE_ARCHIVE
)
# The ctc decision matches its mirror rounding for rounding: no multiply-add fused into one rounding (KEHOACH 3.12).
set_source_files_properties("../../../src/command_ctc/ctc_score.c" PROPERTIES COMPILE_OPTIONS "-ffp-contract=off")
