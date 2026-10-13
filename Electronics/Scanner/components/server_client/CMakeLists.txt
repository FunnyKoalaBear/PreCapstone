idf_component_register(
    SRCS
        "server_client.cpp"

    INCLUDE_DIRS
        "include"

    REQUIRES
        esp_http_client
        wifi
        log
)
