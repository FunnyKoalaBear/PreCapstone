idf_component_register(SRCS "main.cpp"
                    INCLUDE_DIRS "." 
                    REQUIRES led_strip
                    esp_driver_i2c
                    esp-idf-pn532
                    freertos
                    )
