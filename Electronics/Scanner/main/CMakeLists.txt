idf_component_register(SRCS "main.cpp"
                    INCLUDE_DIRS "." 
                    REQUIRES led_strip
                    driver/i2c_master.h)
