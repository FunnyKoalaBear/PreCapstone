#include <stdio.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "setter_display.hpp"


extern "C" void app_main()
{

    //starting project
    printf("APP MAIN\n");
    SETTER_DISPLAY display;

    if (!display.init()) {
        return;
    }
    display.draw_text_centered("hello", 4, SETTER_DISPLAY::BLACK, SETTER_DISPLAY::WHITE);
    vTaskDelay(pdMS_TO_TICKS(1000));

    static const char *const menu_items[] = {
        "Item 01", "Item 02", "Item 03", "Item 04", "Item 05",
        "Item 06", "Item 07", "Item 08", "Item 09", "Item 10",
        "Item 11", "Item 12", "Item 13", "Item 14", "Item 15",
        "Item 16", "Item 17", "Item 18", "Item 19", "Item 20",
    };
    display.run_scrollable_list(menu_items, sizeof(menu_items) / sizeof(menu_items[0]), 2);
}
