# The streaming probe is exported by ml, never committed: make ai-probe writes it here.
if(NOT EXISTS "${CMAKE_CURRENT_LIST_DIR}/probe/models.bin" OR NOT EXISTS "${CMAKE_CURRENT_LIST_DIR}/probe/stream.bin")
    message(FATAL_ERROR "main/probe/ is empty: run make ai-probe (python -m srpipe.tasks.wake.quant probe)")
endif()

idf_component_register(
    SRCS "test_ai_engine.c" "test_stream.cpp"
    PRIV_INCLUDE_DIRS "../../../src"
    REQUIRES ai_engine sys_storage unity esp_partition esp_timer mbedtls espressif__esp-dl
    EMBED_FILES "probe/models.bin" "probe/stream.bin"
    WHOLE_ARCHIVE
)
