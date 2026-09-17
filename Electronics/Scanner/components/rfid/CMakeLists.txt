idf_component_register(
    SRCS
        "rfid.cpp"

    INCLUDE_DIRS
        "include"

    REQUIRES
        esp_driver_gpio
        esp_driver_i2c
        esp-idf-pn532
        freertos
        log
        configuration
)