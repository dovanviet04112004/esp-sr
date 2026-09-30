idf_component_register(
    SRCS "espsr_compare_main.c" "cost.c" "variant_dsp_afe.c" "variant_espsr.c"
    INCLUDE_DIRS "."
    REQUIRES common dsp_spec dsp_afe sys_storage net_wifi test_report esp_netif lwip esp_timer esp_rom
             espressif__esp-sr
)
