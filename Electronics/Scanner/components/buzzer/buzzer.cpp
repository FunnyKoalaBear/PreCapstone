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
}

bool BUZZER::on()
{
}

bool BUZZER::off()
{
}
