#include <stdio.h>
#include <string.h>


#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include <esp_log.h>

#include "rfid.hpp"
#include "button.hpp"
#include "buzzer.hpp"
#include "scanner_display.hpp"
#include "wifi.hpp"
#include "server_client.hpp"


struct scan_msg_t {
    char uid[16]; 
};

struct button_task_args_t {
    BUTTON *button;
    BUZZER *buzzer;
    RFID *rfid;
    TaskHandle_t rfid_task;
    QueueHandle_t scan_queue;

};

struct send_scan_task_args_t {
    SERVER_CLIENT *server; 
};

struct rfid_task_args_t {
    BUZZER *buzzer;
    RFID *rfid;  
};



//test push
static void button_task(void *argument)
{
    auto *args = static_cast<button_task_args_t *>(argument);
    bool was_pressed = false; 

    while (true) {
        
        bool pressed = args->button->clicked(); 

        if (pressed && !was_pressed) {
    
            //notify rfid task 
            xTaskNotifyGive(args->rfid_task);

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
    static const char *TAG = "main";
    auto *args = static_cast<rfid_task_args_t *>(argument);
	uint8_t uid[10];
    uint8_t uid_length = 0; 

    while (true) {
    
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);     // sleeps here until the button gives a notification
    
        if (args->rfid->read_uid(uid, &uid_length)) {
            
            ESP_LOGW(TAG, "RFID Task recieved UID");
            
            //buzz twice now
            args->buzzer->on();
            vTaskDelay(pdMS_TO_TICKS(100));
            args->buzzer->off();
            vTaskDelay(pdMS_TO_TICKS(100));
            args->buzzer->on();
            vTaskDelay(pdMS_TO_TICKS(100));
            args->buzzer->off();

        } else {
            // no card
            ESP_LOGE(TAG, "Card not found");
            return;
        }
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
    SCANNER_DISPLAY display;
    SERVER_CLIENT server;

    //display first: rfid.init() retries forever if the PN532 isn't connected
    if (display.init()) {
        display.draw_text_centered("hello", 4, SCANNER_DISPLAY::BLACK, SCANNER_DISPLAY::WHITE);
    }

    //initializing classes
    wifi.init(); 
    button.init();
    rfid.init();
    buzzer.init();
    // server.send_scan("hsi FROM ESP"); 


    //creating queue
    static QueueHandle_t scan_queue = xQueueCreate(8, sizeof(scan_msg_t));


    //creating task      
    static TaskHandle_t rfid_handle = nullptr;
    static rfid_task_args_t rfid_args{&buzzer, &rfid};
    xTaskCreate(rfid_task, "RFID Scan", 4096 , &rfid_args, 5, &rfid_handle); 

    
    static button_task_args_t button_args{&button, &buzzer, &rfid, rfid_handle, scan_queue };
    xTaskCreate(button_task, "Button Click", 2048 , &button_args, 5, nullptr); 

    xTaskCreate(send_scan_task, "POST request", 8192, scan_queue, 4, nullptr);

    // keep app_main alive: the tasks above hold pointers to its local objects
    while (true) {
        //sleep until the button notifies 
        vTaskDelay(portMAX_DELAY);
    }
}