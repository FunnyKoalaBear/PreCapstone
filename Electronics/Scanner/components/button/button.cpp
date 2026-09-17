#include <stdio.h>
#include <esp_log.h>
#include "driver/gpio.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "button.hpp"


namespace {
    constexpr gpio_num_t BUTTON_PIN = GPIO_NUM_4;
    static const char *TAG = "button";
}


bool BUTTON::init()
{
	esp_err_t err;
	vTaskDelay(1000 / portTICK_PERIOD_MS);
	ESP_LOGI(TAG, "init steel button %d", BUTTON_PIN); 

    //settting up GPIO pin for button
    gpio_config_t myButton = {
        .pin_bit_mask = (1ULL << BUTTON_PIN),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };

    err = gpio_config(&myButton);

    if (err != ESP_OK) {
        ESP_LOGW(TAG, "failed to initialize button");

        vTaskDelay(1000 / portTICK_PERIOD_MS);
    }


    ESP_LOGI(TAG, "initialized button!");
	return true;
}

bool BUTTON::clicked()
{
    // Returns true when pressed (Active-LOW: level drops to 0)
    if (gpio_get_level(BUTTON_PIN) == 0) {
        ESP_LOGI(TAG, "Button pressed");
        return 1; 
    }

    return false; 
}