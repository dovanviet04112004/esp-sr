# The probes are exported by ml, never committed: make ai-probe, or make ai-unit-rnnt, ai-unit-speaker, ai-unit-ctc,
# ai-unit-pitch, ai-unit-wake and ai-unit-detect for those builds, writes them.
if(UNIT_PROFILE STREQUAL "wake")
    set(tests "test_stream.cpp" "test_stream_main.c")
    set(embedded "probe/models.bin" "probe/stream.bin" "probe/wake_models.bin" "probe/wake_branch.bin")
    set(flashed "")
elseif(UNIT_PROFILE STREQUAL "speaker")
    set(tests "test_speaker_probe.cpp")
    set(embedded "probe/speaker_windows.bin")
    set(flashed "probe/speaker_models.bin")
elseif(UNIT_PROFILE STREQUAL "pitch")
    set(tests "test_pitch_probe.cpp")
    set(embedded "probe/pitch_window.bin")
    set(flashed "probe/pitch_models.bin")
elseif(UNIT_PROFILE STREQUAL "detect")
    set(tests "test_detect_probe.cpp")
    set(embedded "probe/detect_expect.bin")
    set(flashed "probe/detect_vision.bin" "probe/detect_broken.bin" "probe/detect_pictures.bin")
elseif(UNIT_PROFILE STREQUAL "gtcrn")
    set(tests "test_gtcrn_timing.c")
    set(embedded "")
    set(flashed "")
elseif(UNIT_PROFILE STREQUAL "cores")
    set(tests "test_cores.cpp")
    set(embedded "")
    set(flashed "")
elseif(UNIT_PROFILE STREQUAL "kernels")
    set(espdl "${CMAKE_CURRENT_LIST_DIR}/../../../src/core/espdl")
    file(GLOB kernels_c "${espdl}/kernels/*.c")
    set(tests "test_kernels.cpp" "kernel_cases_pie.cpp" "kernel_cases_c.cpp" ${kernels_c})
    set(embedded "")
    set(flashed "")
elseif(UNIT_PROFILE STREQUAL "ctc")
    set(tests "test_ctc_net.cpp" "test_ctc_net_main.c")
    set(embedded "probe/ctc_streams.bin")
    set(flashed "probe/ctc_models.bin")
elseif(CONFIG_AI_ENGINE_COMMAND_BACKEND_RNNT)
    set(tests "test_rnnt_probe.cpp")
    set(embedded "probe/rnnt_streams.bin" "probe/rnnt_vectors.bin" "probe/rnnt_windows.bin")
    set(flashed "probe/rnnt_models.bin")
else()
    # Cases run in this order; test_ctc_net.cpp's erases model slot 0, which the ctc cases before it read.
    set(tests "test_ai_engine.c" "test_stream.cpp" "test_kws_probe.cpp" "test_ctc_probe.cpp" "test_ctc_net.cpp"
              "test_ns_probe.cpp" "test_ctc_decide.c")
    set(embedded "probe/models.bin" "probe/stream.bin" "probe/wake_models.bin" "probe/wake_branch.bin"
                 "probe/kws_models.bin" "probe/kws_windows.bin"
                 "probe/ctc_streams.bin" "probe/ctc_decide.bin" "probe/ctc_windows.bin" "probe/ns_streams.bin")
    set(flashed "probe/ctc_models.bin" "probe/ns_models.bin")
endif()
foreach(probe ${embedded} ${flashed})
    if(NOT EXISTS "${CMAKE_CURRENT_LIST_DIR}/${probe}")
        message(FATAL_ERROR "main/${probe} is missing: run make ai-probe, or the make ai-unit-<profile> of this build")
    endif()
endforeach()

idf_component_register(
    SRCS ${tests}
    PRIV_INCLUDE_DIRS "../../../src"
    REQUIRES ai_engine dsp_spec sys_storage unity esp_partition esp_timer mbedtls espressif__esp-dl test_report
    EMBED_FILES ${embedded}
    WHOLE_ARCHIVE
)
# The same cases built twice, each against one implementation of the kernels (ADR-0045).
if(UNIT_PROFILE STREQUAL "kernels")
    set_source_files_properties("kernel_cases_pie.cpp" PROPERTIES INCLUDE_DIRECTORIES "${espdl}/kernels/impl/pie_esp32s3")
    set_source_files_properties("test_kernels.cpp" PROPERTIES INCLUDE_DIRECTORIES "${espdl}/chip/esp32s3")
    set_source_files_properties("kernel_cases_c.cpp" ${kernels_c}
        PROPERTIES INCLUDE_DIRECTORIES "${espdl}/kernels/impl/c;${espdl}/chip/esp32s3")
    # The float kernels repeat the PIE's roundings one by one: no multiply-add fused behind their back.
    set_source_files_properties(${kernels_c} PROPERTIES COMPILE_OPTIONS "-ffp-contract=off")
endif()
# idf.py -D UNIT_LOAN_CAP=<bytes> leaves the speaker's runs no internal block over that, as on the product (KEHOACH 6.5).
if(NOT UNIT_LOAN_CAP)
    set(UNIT_LOAN_CAP 0)
endif()
target_compile_definitions(${COMPONENT_LIB} PRIVATE "UNIT_LOAN_CAP_BYTES=${UNIT_LOAN_CAP}")
