#pragma once

#include <stdint.h>

// Row of push buttons from config::PANEL_BUTTON_PINS, wired GPIO -> button -> GND
class BUTTON_PANEL
{
public:
    static constexpr int MAX_BUTTONS = 16;

    bool init();

    // number of buttons (1..count)
    int count() const;

    // true while button `number` (1-based) is held down
    bool is_down(int number) const;

    // call every ~50ms: returns the number (1-based) of a newly pressed button, or 0
    int pressed();

private:
    uint32_t was_down_ = 0;   // bit i set = button i+1 was down at the last poll
};
