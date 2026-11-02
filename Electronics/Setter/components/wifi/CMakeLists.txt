idf_component_register(
    SRCS
        "wifi.cpp"

    INCLUDE_DIRS
        "include"

    REQUIRES
        esp_wifi
        esp_netif
        esp_event
        nvs_flash
        freertos
        log
)
