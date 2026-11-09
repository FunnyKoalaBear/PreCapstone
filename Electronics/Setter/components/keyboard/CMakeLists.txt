idf_component_register(
    SRCS
        "keyboard.cpp"

    INCLUDE_DIRS
        "include"

    REQUIRES
        esp_driver_uart
        vfs
        log
)
