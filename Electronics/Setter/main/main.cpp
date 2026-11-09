#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <esp_log.h>
#include "esp_netif_sntp.h"

#include "rfid.hpp"
#include "button.hpp"
#include "buzzer.hpp"
#include "setter_display.hpp"
#include "wifi.hpp"
#include "button_panel.hpp"
#include "keyboard.hpp"
#include "setter_app.hpp"
#include "text_ui.hpp"
#include "setter_gui.hpp"


// Panel button N (wired as in configuration.hpp) runs PANEL_ACTIONS[N - 1]; essentials on 1-4
static void (*const PANEL_ACTIONS[])() = {
    setter_up,               // 1: previous item
    setter_down,             // 2: next item
    setter_link_selected,    // 3: link the selected item (then tap a card)
    setter_back,             // 4: back / cancel / reload
    setter_toggle_details,   // 5: details screen
    setter_toggle_drawing,   // 6: drawing screen
    setter_next_view,        // 7: squares / sideways / table
    setter_next_sort,        // 8: filter (sort order)
};


// Typed commands: the SetterUI keys (w s y r q e v f), n/p, a row number or an item code
static void keyboard_task(void *argument)
{
    auto *keyboard = static_cast<KEYBOARD *>(argument);
    char line[32];

    while (true) {
        keyboard->read_line(line, sizeof(line));

        if (strlen(line) == 1) {
            switch (line[0] | 0x20) {   // | 0x20 makes letters lower case
                case 'w': setter_up(); continue;
                case 's': setter_down(); continue;
                case 'y': setter_link_selected(); continue;
                case 'r':
                case 'c': setter_back(); continue;
                case 'q': setter_toggle_details(); continue;
                case 'e': setter_toggle_drawing(); continue;
                case 'v': setter_next_view(); continue;
                case 'f': setter_next_sort(); continue;
                case 'n': setter_next_page(); continue;
                case 'p': setter_prev_page(); continue;
                default: break;
            }
        }

        if (strchr(line, '-')) {
            setter_pick_code(line);
            continue;
        }
        char *end = nullptr;
        long row = strtol(line, &end, 10);
        if (*end == '\0' && row >= 1) {
            setter_pick((int)row - 1);
        } else {
            printf("Unknown input \"%s\". Type a number, an item code, or one of w s y r q e v f n p.\n", line);
        }
    }
}


// The steel push button works as "back" (cancels a card wait)
static void button_task(void *argument)
{
    auto *button = static_cast<BUTTON *>(argument);
    bool was_pressed = false;

    while (true) {
        bool pressed = button->clicked();
        if (pressed && !was_pressed) {
            setter_back();
        }
        was_pressed = pressed;
        vTaskDelay(pdMS_TO_TICKS(50));
    }
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
    printf("APP MAIN (setter)\n");

    // static: the tasks keep pointers to these after app_main returns
    static WIFI wifi;
    static RFID rfid;
    static BUTTON button;
    static BUZZER buzzer;
    static SETTER_DISPLAY display;
    static BUTTON_PANEL panel;
    static KEYBOARD keyboard;
    static TEXT_UI serial_ui;
    static SETTER_GUI gui(&display);

    // the graphic UI if the screen works, otherwise serial monitor only
    SETTER_UI *ui = (display.init() && gui.init()) ? static_cast<SETTER_UI *>(&gui) : &serial_ui;
    ui->show_message("Starting...");

    keyboard.init();
    buzzer.init();
    button.init();
    if (panel.init()) {
        xTaskCreate(panel_task, "Button Panel", 3072, &panel, 5, nullptr);
    }

    ui->show_message("Connecting WiFi...");
    wifi.init();   // keeps retrying in the background if it isn't up yet
    start_clock();

    ui->show_message("Starting RFID reader...");
    rfid.init();   // waits here until the PN532 answers: check its wiring if stuck

    if (!setter_app_start(ui, &rfid, &buzzer)) {
        ui->show_error("Setter failed to start");
        return;
    }
    xTaskCreate(keyboard_task, "Keyboard", 3072, &keyboard, 5, nullptr);
    xTaskCreate(button_task, "Button", 2048, &button, 5, nullptr);
}
