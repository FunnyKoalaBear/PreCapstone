idf_component_register(
    SRCS
        "canvas.cpp"
        "scanner_gui.cpp"

    INCLUDE_DIRS
        "include"

    REQUIRES
        scanner_display
        freertos
        log
        heap
)
