# The probes are exported by ml, never committed: make ai-probe, or make ai-unit-rnnt for the rnnt build, writes them.
if(CONFIG_AI_ENGINE_COMMAND_BACKEND_RNNT)
    set(tests "test_rnnt_probe.cpp")
    set(embedded "probe/rnnt_streams.bin" "probe/rnnt_vectors.bin" "probe/rnnt_windows.bin")
    set(flashed "probe/rnnt_models.bin")
else()
    set(tests "test_ai_engine.c" "test_stream.cpp" "test_kws_probe.cpp" "test_ctc_probe.cpp" "test_ns_probe.cpp"
              "test_ctc_decide.c")
    set(embedded "probe/models.bin" "probe/stream.bin" "probe/kws_models.bin" "probe/kws_windows.bin"
                 "probe/ctc_streams.bin" "probe/ctc_decide.bin" "probe/ctc_windows.bin" "probe/ns_streams.bin")
    set(flashed "probe/ctc_models.bin" "probe/ns_models.bin")
endif()
foreach(probe ${embedded} ${flashed})
    if(NOT EXISTS "${CMAKE_CURRENT_LIST_DIR}/${probe}")
        message(FATAL_ERROR "main/${probe} is missing: run make ai-probe, or make ai-unit-rnnt for the rnnt build")
    endif()
endforeach()

idf_component_register(
    SRCS ${tests}
    PRIV_INCLUDE_DIRS "../../../src"
    REQUIRES ai_engine dsp_spec sys_storage unity esp_partition esp_timer mbedtls espressif__esp-dl
    EMBED_FILES ${embedded}
    WHOLE_ARCHIVE
)
