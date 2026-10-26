idf_component_register(
    SRCS
        "scanner_display.cpp"

    INCLUDE_DIRS
        "include"

    REQUIRES
        esp_lcd
        esp_driver_spi
        esp_driver_gpio
        esp_lcd_st7796
        freertos
        log
        configuration
)
