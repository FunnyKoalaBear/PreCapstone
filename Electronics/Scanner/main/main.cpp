#include <stdio.h>
#include <string.h>


#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include <esp_log.h>

#include "rfid.hpp"
#include "button.hpp"
#include "buzzer.hpp"
#include "display.hpp"
#include "wifi.hpp"
#include "server_client.hpp"


struct scan_msg_t {
    char uid[16]; 
};

struct button_task_args_t {
    BUTTON *button;
    BUZZER *buzzer;
    QueueHandle_t scan_queue;

};

struct send_scan_task_args_t {
    SERVER_CLIENT *server; 
};


//test push
static void button_task(void *argument)
{
    auto *args = static_cast<button_task_args_t *>(argument);
    bool was_pressed = false; 

    while (true) {
        
        bool pressed = args->button->clicked(); 

        if (pressed && !was_pressed) {
            
            args->buzzer->on();
            vTaskDelay(pdMS_TO_TICKS(100));
            args->buzzer->off();

        } else if (!pressed) {
            args->buzzer->off();
        }

        if (pressed && !was_pressed) {
            scan_msg_t msg = {};

            strlcpy(msg.uid, "Hi from task", sizeof(msg.uid));
            xQueueSend(args->scan_queue, &msg, 0);
        }
        
        was_pressed = pressed;
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    
    vTaskDelete(NULL);
}


static void send_scan_task(void *argument) {

    //auto *args = static_cast<send_scan_task_args_t *>(argument);
    auto queue = static_cast<QueueHandle_t>(argument);
    
    SERVER_CLIENT server; 
    scan_msg_t msg; 

    while (true) {  
        if (xQueueReceive(queue, &msg, portMAX_DELAY) == pdTRUE) { 
            
            server.send_scan(msg.uid);
            static const char *TAG = "main";
            ESP_LOGW(TAG, "Network task stack %u", (unsigned)uxTaskGetStackHighWaterMark(nullptr));
        }
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
    WIFI wifi; 
    RFID rfid;
    BUTTON button;
    BUZZER buzzer;
    DISPLAY display;
    SERVER_CLIENT server;

    //display first: rfid.init() retries forever if the PN532 isn't connected
    if (display.init()) {
        display.draw_text_centered("hello", 4, DISPLAY::BLACK, DISPLAY::WHITE);
    }

    //initializing classes
    wifi.init(); 
    button.init();
    rfid.init();
    buzzer.init();
    // server.send_scan("hi FROM ESP"); 


    //creating queue
    static QueueHandle_t scan_queue = xQueueCreate(8, sizeof(scan_msg_t));

    static button_task_args_t button_args{ &button, &buzzer, scan_queue };
    xTaskCreate(button_task, "Button Click", 2048 , &button_args, 5, nullptr); 


    //creating task      
    xTaskCreate(send_scan_task, "POST request", 8192, scan_queue, 4, nullptr);
    xTaskCreate(rfid_task, "RFID Scan", 4096 , &rfid, 5, nullptr); 


    static const char *const menu_items[] = {
        "Item 01", "Item 02", "Item 03", "Item 04", "Item 05",
        "Item 06", "Item 07", "Item 08", "Item 09", "Item 10",
        "Item 11", "Item 12", "Item 13", "Item 14", "Item 15",
        "Item 16", "Item 17", "Item 18", "Item 19", "Item 20",
    };
    display.run_scrollable_list(menu_items, sizeof(menu_items) / sizeof(menu_items[0]), 2);
}