#include <stdio.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "rfid.hpp"
#include "button.hpp"

static void button_task(void *argument)
{
    auto *button = static_cast<BUTTON *>(argument);
    while (true) {
        button->clicked();
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

    //initializing classes
    button.init();
    rfid.init();

    //creating tasks 
    int button_param = 42;
    xTaskCreate(button_task, "Button Click", 2048 , &button_param, 5, nullptr); 

    int rfid_param = 43;
    xTaskCreate(rfid_task, "RFID Scan", 4096 , &rfid_param, 5, nullptr); 


    while (1) {
        rfid.scan();
        button.clicked();
    }
}