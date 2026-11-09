#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <esp_log.h>

#include "rfid.hpp"
#include "button.hpp"
#include "buzzer.hpp"
#include "setter_display.hpp"
#include "wifi.hpp"
#include "button_panel.hpp"
#include "keyboard.hpp"
#include "setter_app.hpp"
#include "text_ui.hpp"


// Turns a typed line into a setter command: "3" picks row 3, "2611-001-01" picks by code, n/p/r/c
static void keyboard_task(void *argument)
{
    auto *keyboard = static_cast<KEYBOARD *>(argument);
    char line[32];

    while (true) {
        keyboard->read_line(line, sizeof(line));

        if (strcasecmp(line, "n") == 0) {
            setter_next_page();
        } else if (strcasecmp(line, "p") == 0) {
            setter_prev_page();
        } else if (strcasecmp(line, "r") == 0) {
            setter_reload();
        } else if (strcasecmp(line, "c") == 0) {
            setter_cancel();
        } else if (strchr(line, '-')) {
            setter_pick_code(line);
        } else {
            char *end = nullptr;
            long row = strtol(line, &end, 10);
            if (*end == '\0' && row >= 1) {
                setter_pick((int)row - 1);
            } else {
                printf("Unknown input \"%s\". Type a row number, an item code, n, p, r or c.\n", line);
            }
        }
    }
}


// The single push button cancels a card wait (or reloads the list)
static void button_task(void *argument)
{
    auto *button = static_cast<BUTTON *>(argument);
    bool was_pressed = false;

    while (true) {
        bool pressed = button->clicked();
        if (pressed && !was_pressed) {
            setter_cancel();
        }
        was_pressed = pressed;
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}


// test: prints the number of each panel button when it is pressed (the UI wires them later)
static void panel_task(void *argument)
{
    auto *panel = static_cast<BUTTON_PANEL *>(argument);

    while (true) {
        int number = panel->pressed();
        if (number != 0) {
            printf("Button %d pressed\n", number);
        }
        vTaskDelay(pdMS_TO_TICKS(50));
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

    // display first, so later steps can say what they are doing
    bool has_display = display.init();
    static TEXT_UI ui(has_display ? &display : nullptr);
    ui.show_message("Starting...");

    keyboard.init();
    buzzer.init();
    button.init();
    if (panel.init()) {
        xTaskCreate(panel_task, "Button Panel", 3072, &panel, 5, nullptr);
    }

    ui.show_message("Connecting WiFi...");
    wifi.init();   // keeps retrying in the background if it isn't up yet

    ui.show_message("Starting RFID reader...");
    rfid.init();   // waits here until the PN532 answers: check its wiring if stuck

    if (!setter_app_start(&ui, &rfid, &buzzer)) {
        ui.show_error("Setter failed to start");
        return;
    }
    xTaskCreate(keyboard_task, "Keyboard", 3072, &keyboard, 5, nullptr);
    xTaskCreate(button_task, "Button", 2048, &button, 5, nullptr);
}
