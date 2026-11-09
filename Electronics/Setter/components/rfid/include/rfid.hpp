#include <stdint.h>

#pragma once

class RFID
{
public:
    bool init();
    bool scan();
    bool read_uid(uint8_t *uid_out, uint8_t *uid_length_out);

    // One quick look (~100 ms) for a card; uid_out needs room for 10 bytes
    bool poll_uid(uint8_t *uid_out, uint8_t *uid_length_out);
};