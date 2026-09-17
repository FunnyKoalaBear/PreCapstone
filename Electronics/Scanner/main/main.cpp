#include <stdio.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "rfid.hpp"
#include "button.hpp"
#include "buzzer.hpp"


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
    //starting projecct
    printf("APP MAIN\n");
    RFID rfid;
    BUTTON button;
    BUZZER buzzer;

    //initializing classes
    button.init();
    rfid.init();
    buzzer.init();

    //creating tasks 
    static button_task_args_t button_args{ &button, &buzzer };
    xTaskCreate(button_task, "Button Click", 2048 , &button_args, 5, nullptr); 

    int rfid_param = 43;
    xTaskCreate(rfid_task, "RFID Scan", 4096 , &rfid_param, 5, nullptr); 


    while (1) {
        rfid.scan();
        button.clicked();
    }
}