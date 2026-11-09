idf_component_register(
    SRCS
        "setter_app.cpp"
        "text_ui.cpp"

    INCLUDE_DIRS
        "include"

    REQUIRES
        server_client
        rfid
        buzzer
        setter_display
        freertos
        log
)
