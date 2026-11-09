#include <stdio.h>

#include <esp_log.h>
#include "driver/uart.h"
#include "driver/uart_vfs.h"

#include "keyboard.hpp"


namespace {
    static const char *TAG = "keyboard";

#ifdef CONFIG_ESP_CONSOLE_UART_NUM
    constexpr uart_port_t PORT = (uart_port_t)CONFIG_ESP_CONSOLE_UART_NUM;
#else
    constexpr uart_port_t PORT = UART_NUM_0;
#endif
}


bool KEYBOARD::init()
{
    esp_err_t err = uart_driver_install(PORT, 256, 0, 0, nullptr, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "uart_driver_install failed: %s", esp_err_to_name(err));
        return false;
    }

    // printf now goes through the same driver, so typed keys and log output don't fight over the port
    uart_vfs_dev_use_driver(PORT);
    return true;
}


int KEYBOARD::read_line(char *line, size_t size)
{
    size_t len = 0;

    while (true) {
        char c;
        if (uart_read_bytes(PORT, &c, 1, portMAX_DELAY) != 1) {
            continue;
        }

        if (c == '\r' || c == '\n') {
            if (len == 0) {
                continue;   // ignore empty lines and the \n after a \r
            }
            uart_write_bytes(PORT, "\r\n", 2);
            line[len] = '\0';
            return (int)len;
        }

        if (c == '\b' || c == 0x7F) {
            if (len > 0) {
                len--;
                uart_write_bytes(PORT, "\b \b", 3);
            }
            continue;
        }

        if (c >= 0x20 && c < 0x7F && len + 1 < size) {
            line[len++] = c;
            uart_write_bytes(PORT, &c, 1);   // echo, the monitor doesn't show what you type
        }
    }
}
