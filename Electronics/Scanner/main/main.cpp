#include <cstdio>

#include <iostream>
#include "led_strip.h"
#include "driver/i2c_master.h"


led_strip_handle_t led_strip;

extern "C" void app_main(void)
{
    led_strip_set_pixel(led_strip, 0, 255, 0, 0);  // Red
    led_strip_refresh(led_strip);


    while (1) {
        printf("Hello ESP32!\n");
        sleep(1);
    }
}
