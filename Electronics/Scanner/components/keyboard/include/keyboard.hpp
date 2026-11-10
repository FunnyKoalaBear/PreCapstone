#pragma once

#include <stddef.h>

// Reads lines typed on the PC keyboard through the USB cable (the serial monitor, UART0)
class KEYBOARD
{
public:
    // Installs the UART driver so reads wait for keys instead of returning nothing
    bool init();

    // Waits for Enter; echoes what is typed, handles Backspace. Returns the line length.
    int read_line(char *line, size_t size);
};
