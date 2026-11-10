idf_component_register(
    SRCS
        "canvas.cpp"
        "tft_gfx.cpp"
        "scanner_gui.cpp"
        "scanner_ui.cpp"

    INCLUDE_DIRS
        "include"

    REQUIRES
        scanner_display
        server_client
        freertos
        log
        heap
)
