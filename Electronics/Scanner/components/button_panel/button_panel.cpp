#include <stdio.h>
#include <esp_log.h>
#include "driver/gpio.h"

#include "button_panel.hpp"
#include "configuration.hpp"


namespace {
    static const char *TAG = "button_panel";

    static_assert(config::PANEL_BUTTON_COUNT <= BUTTON_PANEL::MAX_BUTTONS, "too many panel buttons");
}


bool BUTTON_PANEL::init()
{
    ESP_LOGI(TAG, "init %d panel buttons", config::PANEL_BUTTON_COUNT);

    uint64_t mask = 0;
    for (gpio_num_t pin : config::PANEL_BUTTON_PINS) {
        mask |= 1ULL << pin;
    }

    // active-low: internal pull-up holds the pin at 1, a press pulls it to 0
    gpio_config_t cfg = {
        .pin_bit_mask = mask,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };

    if (gpio_config(&cfg) != ESP_OK) {
        ESP_LOGW(TAG, "failed to initialize panel buttons");
        return false;
    }

    // a button already reading pressed at boot is usually a wiring short to GND
    for (int n = 1; n <= count(); n++) {
        if (is_down(n)) {
            ESP_LOGW(TAG, "button %d (GPIO %d) reads pressed at boot, check its wiring",
                     n, config::PANEL_BUTTON_PINS[n - 1]);
            was_down_ |= 1u << (n - 1);   // don't report it as a new press
        }
    }

    ESP_LOGI(TAG, "initialized panel buttons!");
    return true;
}

int BUTTON_PANEL::count() const
{
    return config::PANEL_BUTTON_COUNT;
}

bool BUTTON_PANEL::is_down(int number) const
{
    if (number < 1 || number > count()) {
        return false;
    }
    return gpio_get_level(config::PANEL_BUTTON_PINS[number - 1]) == 0;
}

int BUTTON_PANEL::pressed()
{
    int new_press = 0;

    for (int n = 1; n <= count(); n++) {
        const uint32_t bit = 1u << (n - 1);

        if (!is_down(n)) {
            was_down_ &= ~bit;
        } else if (!(was_down_ & bit) && new_press == 0) {
            // report one press per call; others stay unmarked and come out on the next poll
            was_down_ |= bit;
            new_press = n;
        }
    }

    return new_press;
}
