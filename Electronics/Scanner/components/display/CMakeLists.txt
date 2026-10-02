idf_component_register(
    SRCS
        "display.cpp"

    INCLUDE_DIRS
        "include"

    REQUIRES
        esp_lcd
        esp_driver_spi
        esp_driver_gpio
        esp_lcd_st7796
        esp_lcd_touch
        esp_lcd_touch_xpt2046
        freertos
        log
        configuration
)
