#include <stdio.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "rfid.hpp"
#include "button.hpp"
#include "buzzer.hpp"
#include "display.hpp"
#include "wifi.hpp"


struct button_task_args_t {
    BUTTON *button;
    BUZZER *buzzer;
};

//test push

static void button_task(void *argument)
{
    auto *args = static_cast<button_task_args_t *>(argument);

    while (true) {
        
        if (args->button->clicked()) {
            args->buzzer->on();
        }
        else args->buzzer->off();

        
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    
    vTaskDelete(NULL);
}

static void rfid_task(void *argument)
{
    auto *rfid = static_cast<RFID *>(argument);
    while (true) {
        rfid->scan();
        vTaskDelay(pdMS_TO_TICKS(100));
    }
    vTaskDelete(NULL);
}


extern "C" void app_main()
{
    WIFI wifi; 
    wifi.init(); 

    //starting projecct
    printf("APP MAIN\n");
    RFID rfid;
    BUTTON button;
    BUZZER buzzer;
    DISPLAY display;

    //display first: rfid.init() retries forever if the PN532 isn't connected
    if (display.init()) {
        display.draw_text_centered("hello", 4, DISPLAY::BLACK, DISPLAY::WHITE);
    }

    //initializing classes
    button.init();
    rfid.init();
    buzzer.init();

    //creating tasks 
    static button_task_args_t button_args{ &button, &buzzer };
    xTaskCreate(button_task, "Button Click", 2048 , &button_args, 5, nullptr); 

    int rfid_param = 43;
    xTaskCreate(rfid_task, "RFID Scan", 4096 , &rfid_param, 5, nullptr); 


    static const char *const menu_items[] = {
        "Item 01", "Item 02", "Item 03", "Item 04", "Item 05",
        "Item 06", "Item 07", "Item 08", "Item 09", "Item 10",
        "Item 11", "Item 12", "Item 13", "Item 14", "Item 15",
        "Item 16", "Item 17", "Item 18", "Item 19", "Item 20",
    };
    display.run_scrollable_list(menu_items, sizeof(menu_items) / sizeof(menu_items[0]), 2);
}