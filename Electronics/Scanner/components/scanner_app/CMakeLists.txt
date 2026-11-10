idf_component_register(
    SRCS
        "scanner_app.cpp"

    INCLUDE_DIRS
        "include"

    REQUIRES
        scanner_gui
        server_client
        rfid
        buzzer
        freertos
        log
)
