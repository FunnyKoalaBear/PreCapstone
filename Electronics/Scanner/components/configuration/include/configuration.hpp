#pragma once

#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"

namespace config {

    // Button
    constexpr gpio_num_t BUTTON_PIN = GPIO_NUM_4;

    // RFID (PN532 over I2C)
    constexpr gpio_num_t RFID_SCL_PIN   = GPIO_NUM_10;
    constexpr gpio_num_t RFID_SDA_PIN   = GPIO_NUM_11;
    constexpr gpio_num_t RFID_RESET_PIN = GPIO_NUM_NC;
    constexpr gpio_num_t RFID_IRQ_PIN   = GPIO_NUM_NC;

    // Buzzer (TODO: set to actual wired pin)
    constexpr gpio_num_t BUZZER_PIN = GPIO_NUM_NC;

    // Shared timing
    constexpr TickType_t INIT_RETRY_DELAY = pdMS_TO_TICKS(1000);

}  // namespace config