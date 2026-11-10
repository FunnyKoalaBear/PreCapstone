idf_component_register(
    SRCS
        "canvas.cpp"
        "setter_gui.cpp"

    INCLUDE_DIRS
        "include"

    # Japanese bitmap fonts made by tools/make_jp_font.py
    EMBED_FILES
        "fonts/jp16.bin"
        "fonts/jp24.bin"

    REQUIRES
        setter_app
        setter_display
        freertos
        log
        heap
)
