idf_component_register(
    SRCS
        "canvas.cpp"
        "setter_gui.cpp"

    INCLUDE_DIRS
        "include"

    REQUIRES
        setter_app
        setter_display
        freertos
        log
        heap
)
