#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>


#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <esp_log.h>
#include "esp_netif_sntp.h"

#include "rfid.hpp"
#include "button.hpp"
#include "buzzer.hpp"
#include "scanner_display.hpp"
#include "wifi.hpp"
#include "button_panel.hpp"
#include "keyboard.hpp"
#include "scanner_ui.hpp"


// Panel button N runs PANEL_ACTIONS[N - 1]: the sketch's Y / N / E keys
static void (*const PANEL_ACTIONS[])() = {
    scanner_ui_yes,       // 1: YES, log the scan
    scanner_ui_no,        // 2: NO, reject it
    scanner_ui_enhance,   // 3: blueprint view on / off
};

struct button_task_args_t {
    BUTTON *button;
    BUZZER *buzzer;
    TaskHandle_t rfid_task;
};

struct rfid_task_args_t {
    BUZZER *buzzer;
    RFID *rfid;
};

struct keyboard_task_args_t {
    KEYBOARD *keyboard;
    TaskHandle_t rfid_task;
};



// The steel button starts a card read (5 s window) and beeps
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
        }
        
        was_pressed = pressed;
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    
    vTaskDelete(NULL);
}


static void rfid_task(void *argument)
{
    static const char *TAG = "main";
    auto *args = static_cast<rfid_task_args_t *>(argument);
	uint8_t uid[10];
    uint8_t uid_length = 0; 
    char uid_hex[24];   // up to 10 UID bytes as hex + NUL

    while (true) {
    
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);     // sleeps here until the button gives a notification
    
        if (args->rfid->read_uid(uid, &uid_length)) {
            
            for (uint8_t i = 0; i < uid_length; i++) {
                    snprintf(uid_hex + 2 * i, sizeof(uid_hex) - 2 * i, "%02X", uid[i]);   // 0d 11 69 6b -> "0D11696B"
            }
            ESP_LOGW(TAG, "RFID Task recieved UID %s", uid_hex);
            scanner_ui_card(uid_hex);   // the scanner task asks the server and shows the order
            
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
            scanner_ui_no_card();
            continue;
        }
    }

    vTaskDelete(NULL);
}


static void panel_task(void *argument)
{
    auto *panel = static_cast<BUTTON_PANEL *>(argument);
    constexpr int ACTION_COUNT = sizeof(PANEL_ACTIONS) / sizeof(PANEL_ACTIONS[0]);

    while (true) {
        int number = panel->pressed();
        if (number >= 1 && number <= ACTION_COUNT) {
            PANEL_ACTIONS[number - 1]();
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }

    vTaskDelete(NULL);
}


// The sketch's serial controls: SPACE / R = card read (like the steel button), Y, N, E
static void keyboard_task(void *argument)
{
    auto *args = static_cast<keyboard_task_args_t *>(argument);

    while (true) {
        switch (tolower(args->keyboard->read_key())) {
            case 'r':
            case ' ': xTaskNotifyGive(args->rfid_task); break;
            case 'y': scanner_ui_yes(); break;
            case 'n': scanner_ui_no(); break;
            case 'e': scanner_ui_enhance(); break;
            default: break;
        }
    }
}


// Clock for the header: Japan time from an internet time server (shows --:-- until it answers)
static void start_clock()
{
    setenv("TZ", "JST-9", 1);
    tzset();

    esp_sntp_config_t cfg = {};
    cfg.start = true;
    cfg.ip_event_to_renew = IP_EVENT_STA_GOT_IP;
    cfg.num_of_servers = 1;
    cfg.servers[0] = "pool.ntp.org";
    if (esp_netif_sntp_init(&cfg) != ESP_OK) {
        ESP_LOGW("main", "SNTP not started, the clock will show --:--");
    }
}


extern "C" void app_main()
{

    //starting projecct
    printf("APP MAIN\n");

    // static: the tasks keep pointers to these after app_main returns
    static WIFI wifi; 
    static RFID rfid;
    static BUTTON button;
    static BUZZER buzzer;
    static SCANNER_DISPLAY display;
    static BUTTON_PANEL panel;
    static KEYBOARD keyboard;

    //display first: rfid.init() retries forever if the PN532 isn't connected
    scanner_ui_start(display.init() ? &display : nullptr);

    if (panel.init()) {
        xTaskCreate(panel_task, "Button Panel", 3072, &panel, 5, nullptr);
    }
    keyboard.init();

    //initializing classes
    wifi.init(); 
    start_clock();
    button.init();
    rfid.init();   // waits here until the PN532 answers: check its wiring if stuck
    buzzer.init();


    //creating tasks
    static TaskHandle_t rfid_handle = nullptr;
    static rfid_task_args_t rfid_args{&buzzer, &rfid};
    xTaskCreate(rfid_task, "RFID Scan", 4096 , &rfid_args, 5, &rfid_handle); 

    static button_task_args_t button_args{&button, &buzzer, rfid_handle};
    xTaskCreate(button_task, "Button Click", 2048 , &button_args, 5, nullptr); 

    static keyboard_task_args_t keyboard_args{&keyboard, rfid_handle};
    xTaskCreate(keyboard_task, "Keyboard", 3072, &keyboard_args, 5, nullptr);

    printf("=====================================================\n");
    printf(" SCANNER UI READY - Serial Keyboard Controls:\n");
    printf("  [SPACE / R] = Read a card (same as the steel button)\n");
    printf("  [Y] = YES (Confirm)   panel button 1\n");
    printf("  [N] = NO (Reject)     panel button 2\n");
    printf("  [E] = Enhance Blueprint  panel button 3\n");
    printf("=====================================================\n");
    // app_main may return now: everything the tasks use is static
}
