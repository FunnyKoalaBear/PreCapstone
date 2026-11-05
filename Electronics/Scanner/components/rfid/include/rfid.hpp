#include <stdint.h>

#pragma once

class RFID
{
public:
    bool init();
    bool scan();
    bool read_uid(uint8_t *uid_out, uint8_t *uid_length_out);
};