#pragma once

#include <stdint.h>

class WIFI
{
public:
    // Starts WiFi and waits for an IP address. Returns false if it isn't connected in time,
    // but keeps retrying in the background, so connected() may become true later.
    bool init();
    bool connected() const;
    bool wait_connected(uint32_t timeout_ms) const;
};
