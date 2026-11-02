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

    // Buzzer
    constexpr gpio_num_t BUZZER_PIN = GPIO_NUM_5;

    // TFT display (ST7796S over SPI). The touch controller's T_CLK / T_DIN / T_DO
    // share the SCK / MOSI / MISO wires below.
    constexpr gpio_num_t TFT_CS_PIN        = GPIO_NUM_6;
    constexpr gpio_num_t TFT_RESET_PIN     = GPIO_NUM_7;
    constexpr gpio_num_t TFT_DC_PIN        = GPIO_NUM_8;
    constexpr gpio_num_t TFT_MOSI_PIN      = GPIO_NUM_9;
    constexpr gpio_num_t TFT_SCK_PIN       = GPIO_NUM_12;
    constexpr gpio_num_t TFT_MISO_PIN      = GPIO_NUM_13;
    constexpr gpio_num_t TFT_BACKLIGHT_PIN = GPIO_NUM_NC;   // LED wired to 3.3V; set to GPIO_NUM_14 if wired to the ESP32

    // Touch controller (XPT2046)
    constexpr gpio_num_t TOUCH_CS_PIN  = GPIO_NUM_15;
    constexpr gpio_num_t TOUCH_IRQ_PIN = GPIO_NUM_16;

    // Shared timing
    constexpr TickType_t INIT_RETRY_DELAY = pdMS_TO_TICKS(1000);

}  // namespace config