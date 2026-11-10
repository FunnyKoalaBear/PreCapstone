idf_component_register(
    SRCS
        "canvas.cpp"
        "scanner_gui.cpp"

    INCLUDE_DIRS
        "include"

    # Japanese bitmap fonts, the same files as the Setter's (made by Electronics/Setter/tools/make_jp_font.py)
    EMBED_FILES
        "fonts/jp16.bin"
        "fonts/jp24.bin"

    REQUIRES
        scanner_display
        freertos
        log
        heap
)
