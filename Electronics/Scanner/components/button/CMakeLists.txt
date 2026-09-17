idf_component_register(
    SRCS
        "button.cpp"

    INCLUDE_DIRS
        "include"

    REQUIRES
        esp_driver_gpio
        freertos
        log
        configuration
)