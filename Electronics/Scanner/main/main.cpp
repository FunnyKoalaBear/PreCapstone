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
#include "scanner_gui.hpp"
#include "scanner_app.hpp"


// Panel button N (wired as in configuration.hpp) runs PANEL_ACTIONS[N - 1]
static void (*const PANEL_ACTIONS[])() = {
    scanner_yes,       // 1: YES, confirm the station (sends the scan)
    scanner_no,        // 2: NO, reject (nothing is sent)
    scanner_enhance,   // 3: big blueprint view on/off
};


// Typed commands, the same keys as scanner_ui.ino: r = card, y, n, e, plus t = test tap without a card
static void keyboard_task(void *argument)
{
    auto *keyboard = static_cast<KEYBOARD *>(argument);
    char line[16];

    while (true) {
        keyboard->read_line(line, sizeof(line));

        switch (strlen(line) == 1 ? (line[0] | 0x20) : 0) {   // | 0x20 makes letters lower case
            case 'r': scanner_request_card(); break;
            case 't': scanner_test_tap(); break;
            case 'y': scanner_yes(); break;
            case 'n': scanner_no(); break;
            case 'e': scanner_enhance(); break;
            default:
                printf("Unknown input \"%s\". Type r (card), t (test tap), y, n or e.\n", line);
                break;
        }
    }
}


// The steel button on GPIO 4 opens the 5 s window to tap a card
static void button_task(void *argument)
{
    auto *button = static_cast<BUTTON *>(argument);
    bool was_pressed = false;

    while (true) {
        bool pressed = button->clicked();
        if (pressed && !was_pressed) {
            scanner_request_card();
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
    printf("APP MAIN (scanner)\n");

    // static: the tasks keep pointers to these after app_main returns
    static WIFI wifi;
    static RFID rfid;
    static BUTTON button;
    static BUZZER buzzer;
    static SCANNER_DISPLAY display;
    static BUTTON_PANEL panel;
    static KEYBOARD keyboard;
    static SCANNER_GUI gui(&display);

    // display first, so later steps can say what they are doing; without it the serial monitor still works
    if (!(display.init() && gui.init())) {
        ESP_LOGW("main", "no display: use the serial monitor");
    }
    gui.show_banner("STARTING...", "CONNECTING WIFI", banner_t::INFO);

    keyboard.init();
    buzzer.init();
    button.init();
    if (panel.init()) {
        xTaskCreate(panel_task, "Button Panel", 3072, &panel, 5, nullptr);
    }

    wifi.init();   // keeps retrying in the background if it isn't up yet
    start_clock();

    gui.show_banner("STARTING...", "RFID READER", banner_t::INFO);
    rfid.init();   // waits here until the PN532 answers: check its wiring if stuck

    if (!scanner_app_start(&gui, &rfid, &buzzer)) {
        gui.show_banner("START FAILED", "RESTART THE SCANNER", banner_t::BAD);
        return;
    }
    xTaskCreate(keyboard_task, "Keyboard", 3072, &keyboard, 5, nullptr);
    xTaskCreate(button_task, "Button", 2048, &button, 5, nullptr);
}
