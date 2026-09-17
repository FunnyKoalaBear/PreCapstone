#include <stdio.h>
#include <esp_log.h>
#include "driver/gpio.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "buzzer.hpp"
#include "configuration.hpp"


namespace {
    static const char *TAG = "buzzer";
}


bool BUZZER::init()
{
    esp_err_t err;
	vTaskDelay(1000 / portTICK_PERIOD_MS);
	ESP_LOGI(TAG, "init buzzer %d", config::BUZZER_PIN); 

    //settting up GPIO pin for button
    gpio_config_t myBuzzer = {
        .pin_bit_mask = (1ULL << config::BUZZER_PIN),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };

    err = gpio_config(&myBuzzer);

    if (err != ESP_OK) {
        ESP_LOGW(TAG, "failed to initialize button");

        vTaskDelay(1000 / portTICK_PERIOD_MS);
    }

    ESP_LOGI(TAG, "intialized buzzer!");
	return true;

}

bool BUZZER::on()
{
    gpio_set_level(config::BUZZER_PIN, 1);
    ESP_LOGI(TAG, "Buzzer beep");
    return 1; 
}

bool BUZZER::off()
{
    gpio_set_level(config::BUZZER_PIN, 0);
    //ESP_LOGI(TAG, "Buzzer off");
    return 0;
}

