#include <stdio.h>
#include <string.h>

#include <esp_log.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "scanner_app.hpp"
#include "scanner_gui.hpp"
#include "server_client.hpp"
#include "rfid.hpp"
#include "buzzer.hpp"


namespace {
    static const char *TAG = "scanner_app";

    constexpr int BANNER_MS = 1500;   // how long a result stays before the screen goes back to idle
    constexpr int TICK_MS = 1000;     // gui->tick() while waiting (clock)

    // Sample orders from scanner_ui.ino, only for the test tap (t): the screen without a card or server
    const scanner_order_t SAMPLE_ORDERS[] = {
        {"#2103", "INVOLUTE SPUR GEAR 24T", 3, 6, false, "STARTED", "CNC HOBBING", "06/30", "07/05",
         "IXZOQ MFG", "HD-B0031", "#2103-A", "PN-2103", "4140 STEEL", ""},
        {"#1940", "PLANETARY CARRIER SET", 2, 6, false, "FINISHED", "5-AXIS MILL", "06/24", "06/28",
         "AERO DYNAMICS", "HD-B0042", "#1940-C", "PN-1940", "7075-T6 AL", "Box 1 of 2"},
        {"#1985", "FLANGED BEARING HOUSING", 4, 6, false, "SENT_OUT", "PRECISION BORE", "06/25", "06/29",
         "KAWASAKI HEAVY", "HD-B0053", "#1985-B", "PN-1985", "GG25 IRON", ""},
    };
    constexpr int SAMPLE_COUNT = sizeof(SAMPLE_ORDERS) / sizeof(SAMPLE_ORDERS[0]);

    enum class cmd_t { REQUEST_CARD, YES, NO, ENHANCE, TEST_TAP };

    QueueHandle_t queue = nullptr;
    SCANNER_GUI *gui = nullptr;
    RFID *rfid = nullptr;
    BUZZER *buzzer = nullptr;


    void send(cmd_t cmd)
    {
        if (queue) {
            xQueueSend(queue, &cmd, 0);   // never block the caller (button task, keyboard task)
        }
    }


    void beep(int times, int on_ms)
    {
        for (int i = 0; i < times; i++) {
            buzzer->on();
            vTaskDelay(pdMS_TO_TICKS(on_ms));
            buzzer->off();
            vTaskDelay(pdMS_TO_TICKS(80));
        }
    }


    // Shows a banner, prints it, and waits so it can be read
    void banner(const char *title, const char *subtitle, banner_t kind, int hold_ms)
    {
        printf("\n>>> %s: %s\n", title, subtitle);
        gui->show_banner(title, subtitle, kind);
        if (hold_ms > 0) {
            vTaskDelay(pdMS_TO_TICKS(hold_ms));
        }
    }


    void show_order(const scanner_order_t &o, bool enhanced, bool sample)
    {
        printf("\n=== %s  %s%s ===\n step %d of %d: %s %s | %s to %s | %s %s\n"
               "y = YES (record it)   n = NO (don't)   e = blueprint view\n",
               o.id, o.part, sample ? "  (sample data)" : "", o.finished ? o.total : o.pos, o.total,
               o.finished ? "route complete" : o.event, o.process, o.start, o.end, o.customer, o.box);
        gui->show_order(o, enhanced);
    }


    // "2026-12-01" -> "12/01"
    void mmdd(const char *iso, char *out, size_t size)
    {
        if (strlen(iso) >= 10) {
            snprintf(out, size, "%.2s/%.2s", iso + 5, iso + 8);
        } else {
            strlcpy(out, "--/--", size);
        }
    }


    // What the order card shows, from the server's answer
    scanner_order_t to_order(const tag_info_t &t)
    {
        scanner_order_t o = {};
        strlcpy(o.id, t.item, sizeof(o.id));
        strlcpy(o.part, t.part, sizeof(o.part));
        o.pos = t.step;
        o.total = t.steps;
        o.finished = (t.state == lookup_state_t::FINISHED);
        strlcpy(o.event, t.event, sizeof(o.event));
        strlcpy(o.process, t.process, sizeof(o.process));
        mmdd(t.ordered, o.start, sizeof(o.start));
        mmdd(t.due, o.end, sizeof(o.end));
        strlcpy(o.customer, t.customer, sizeof(o.customer));
        strlcpy(o.order_number, t.order_number, sizeof(o.order_number));
        strlcpy(o.dwg, t.drawing, sizeof(o.dwg));
        strlcpy(o.part_no, t.part_number, sizeof(o.part_no));
        strlcpy(o.mat, t.material, sizeof(o.mat));
        strlcpy(o.box, t.box, sizeof(o.box));
        return o;
    }


    // Opens the 5 s window and reads a card. Writes the UID as hex, e.g. "0D11696B".
    bool read_card(char *uid_hex, size_t size)
    {
        uint8_t uid[10];
        uint8_t uid_length = 0;

        banner("TAP A CARD", "WITHIN 5 SECONDS", banner_t::INFO, 0);
        beep(1, 100);

        if (!rfid->read_uid(uid, &uid_length)) {
            return false;
        }

        uid_hex[0] = '\0';
        for (uint8_t i = 0; i < uid_length && 2 * i + 2 < size; i++) {
            snprintf(uid_hex + 2 * i, size - 2 * i, "%02X", uid[i]);   // 0d 11 69 6b -> "0D11696B"
        }
        return true;
    }


    // Card -> ask the server which box it is. True (and fills `order`) when there is an order to show.
    bool look_up(SERVER_CLIENT &server, const char *uid, scanner_order_t &order)
    {
        banner("LOOKING UP...", uid, banner_t::INFO, 0);
        const tag_info_t info = server.lookup(uid);

        switch (info.state) {
            case lookup_state_t::OPEN:
            case lookup_state_t::FINISHED:
                order = to_order(info);
                beep(2, 100);
                return true;
            case lookup_state_t::UNASSIGNED:
                beep(1, 600);
                banner("TAG NOT ASSIGNED", "LINK IT ON THE SETTER FIRST", banner_t::BAD, BANNER_MS);
                return false;
            case lookup_state_t::BAD_KEY:
                beep(1, 600);
                banner("DEVICE KEY ERROR", "CHECK THIS SCANNER'S API KEY", banner_t::BAD, BANNER_MS);
                return false;
            default:
                beep(1, 600);
                banner("SERVER ERROR", "COULD NOT LOOK UP THE CARD", banner_t::BAD, BANNER_MS);
                return false;
        }
    }


    // YES: POST the scan and show what the server answered
    void confirm(SERVER_CLIENT &server, const char *uid)
    {
        banner("SENDING...", uid, banner_t::INFO, 0);
        const scan_result_t result = server.scan(uid);
        char line[96];

        switch (result.state) {
            case scan_state_t::RECORDED:
                snprintf(line, sizeof(line), "%s %s %s", result.item, result.event, result.process);
                beep(2, 100);
                banner("STATION CONFIRMED", line, banner_t::GOOD, BANNER_MS);
                return;
            case scan_state_t::FINISHED:
                snprintf(line, sizeof(line), "%s IS COMPLETE", result.item);
                beep(2, 100);
                banner("ALREADY FINISHED", line, banner_t::GOOD, BANNER_MS);
                return;
            case scan_state_t::DUPLICATE:
                snprintf(line, sizeof(line), "%s NOT LOGGED TWICE", result.item);
                beep(1, 100);
                banner("ALREADY SCANNED", line, banner_t::INFO, BANNER_MS);
                return;
            case scan_state_t::UNASSIGNED:
                beep(1, 600);
                banner("TAG NOT ASSIGNED", "LINK IT ON THE SETTER FIRST", banner_t::BAD, BANNER_MS);
                return;
            case scan_state_t::BAD_KEY:
                beep(1, 600);
                banner("DEVICE KEY ERROR", "CHECK THIS SCANNER'S API KEY", banner_t::BAD, BANNER_MS);
                return;
            case scan_state_t::INVALID:
                beep(1, 600);
                banner("REQUEST ERROR", "SCAN NOT SAVED", banner_t::BAD, BANNER_MS);
                return;
            default:
                beep(1, 600);
                banner("SERVER ERROR", "SCAN NOT SAVED, TRY AGAIN", banner_t::BAD, BANNER_MS);
                return;
        }
    }


    void scanner_task(void *argument)
    {
        SERVER_CLIENT server;
        int sample = -1;              // last sample order used by a test tap
        bool order_open = false;
        bool enhanced = false;
        scanner_order_t order = {};   // what is on screen
        char uid[24] = "";            // card behind the open order; empty for a test tap

        printf("\nScanner ready. Press the button (or type r) and tap a card. t = test tap without a card.\n");
        gui->show_idle();

        while (true) {
            cmd_t cmd;
            if (xQueueReceive(queue, &cmd, pdMS_TO_TICKS(TICK_MS)) != pdTRUE) {
                gui->tick();
                continue;
            }

            if (!order_open) {
                if (cmd == cmd_t::REQUEST_CARD) {
                    bool got = read_card(uid, sizeof(uid));
                    if (got) {
                        ESP_LOGI(TAG, "card %s", uid);
                        got = look_up(server, uid, order);
                    } else {
                        beep(1, 600);
                        banner("NO CARD READ", "PRESS THE BUTTON AND TRY AGAIN", banner_t::BAD, BANNER_MS);
                    }
                    if (!got) {
                        gui->show_idle();
                        xQueueReset(queue);
                        continue;
                    }
                } else if (cmd == cmd_t::TEST_TAP) {
                    uid[0] = '\0';
                    sample = (sample + 1) % SAMPLE_COUNT;
                    order = SAMPLE_ORDERS[sample];
                } else {
                    continue;   // YES / NO / ENHANCE mean nothing without an order on screen
                }

                enhanced = false;
                order_open = true;
                show_order(order, enhanced, uid[0] == '\0');
                xQueueReset(queue);   // drop presses made during the card wait
                continue;
            }

            switch (cmd) {
                case cmd_t::ENHANCE:
                    enhanced = !enhanced;
                    gui->show_order(order, enhanced);
                    continue;

                case cmd_t::YES:
                    if (uid[0]) {
                        confirm(server, uid);
                    } else {
                        banner("STATION CONFIRMED", "TEST ONLY, NOTHING SENT", banner_t::GOOD, BANNER_MS);
                    }
                    break;

                case cmd_t::NO: {
                    char line[64];
                    snprintf(line, sizeof(line), "%s NOT LOGGED", order.id);
                    beep(1, 300);
                    banner("SCAN REJECTED", line, banner_t::BAD, BANNER_MS);
                    break;
                }

                default:
                    continue;   // a new card request waits until this order is answered
            }

            order_open = false;
            gui->show_idle();
            xQueueReset(queue);
        }
    }
}


bool scanner_app_start(SCANNER_GUI *scanner_gui, RFID *scanner_rfid, BUZZER *scanner_buzzer)
{
    gui = scanner_gui;
    rfid = scanner_rfid;
    buzzer = scanner_buzzer;

    queue = xQueueCreate(8, sizeof(cmd_t));
    if (!queue) {
        ESP_LOGE(TAG, "no memory for the command queue");
        return false;
    }

    // 12 KB: HTTP client + cJSON + a 1.5 KB reply buffer and the tag info on the stack
    return xTaskCreate(scanner_task, "Scanner", 12288, nullptr, 5, nullptr) == pdPASS;
}


void scanner_request_card() { send(cmd_t::REQUEST_CARD); }
void scanner_yes()          { send(cmd_t::YES); }
void scanner_no()           { send(cmd_t::NO); }
void scanner_enhance()      { send(cmd_t::ENHANCE); }
void scanner_test_tap()     { send(cmd_t::TEST_TAP); }
