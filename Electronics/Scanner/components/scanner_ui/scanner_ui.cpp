#include <stdio.h>
#include <string.h>

#include <esp_log.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "scanner_ui.hpp"
#include "scanner_gui.hpp"
#include "server_client.hpp"


namespace {
    static const char *TAG = "SCANNER";

    constexpr int TICK_MS = 100;            // banner timeout and clock checks
    constexpr int BANNER_MS = 1000;         // the sketch's banner time (YES / NO)
    constexpr int ERROR_BANNER_MS = 2000;   // banners the sketch doesn't have, a little longer to read

    using banner_t = SCANNER_GUI::banner_t;

    enum class cmd_type_t { CARD, NO_CARD, YES, NO, ENHANCE };

    struct cmd_t {
        cmd_type_t type;
        char uid[24];
    };

    QueueHandle_t queue = nullptr;
    SCANNER_GUI *gui = nullptr;   // nullptr: serial monitor only

    // what the modal shows; only the scanner task touches these
    scan_preview_t order = {};
    bool modal_open = false;      // isModalOpen: YES / NO / E only work while it is


    void send(cmd_type_t type, const char *uid = nullptr)
    {
        cmd_t cmd = {};
        cmd.type = type;
        if (uid) {
            strlcpy(cmd.uid, uid, sizeof(cmd.uid));
        }
        if (!queue || xQueueSend(queue, &cmd, 0) != pdTRUE) {   // never block the RFID, button or keyboard task
            ESP_LOGW(TAG, "input dropped (queue full or not started)");
        }
    }


    void banner(const char *title, const char *subtitle, banner_t color, int ms, bool keep_order)
    {
        ESP_LOGI(TAG, "%s - %s", title, subtitle);
        if (gui) {
            gui->show_banner(title, subtitle, color, ms, keep_order);
        }
    }


    // triggerRfidScan(): ask the server what this tag would record and show its order
    void on_card(SERVER_CLIENT &server, const char *uid)
    {
        const scan_preview_t p = server.preview(uid);

        if (p.state == preview_state_t::NEXT) {
            order = p;
            modal_open = true;
            ESP_LOGI(TAG, "RFID Tapped. Showing Order: %s (step %d/%d, %s %s)", p.item, p.step, p.steps, p.process, p.event_type);
            if (gui) {
                gui->show_order(order);
            }
            return;
        }

        modal_open = false;
        char subtitle[64];
        switch (p.state) {
            case preview_state_t::FINISHED:
                snprintf(subtitle, sizeof(subtitle), "ORDER %s: NOTHING LEFT TO LOG", p.item);
                banner("ROUTE COMPLETE", subtitle, banner_t::BAD, ERROR_BANNER_MS, false);
                break;
            case preview_state_t::UNASSIGNED:
                banner("TAG NOT ASSIGNED", "LINK IT ON THE TAGS PAGE", banner_t::BAD, ERROR_BANNER_MS, false);
                break;
            case preview_state_t::BAD_KEY:
                banner("DEVICE KEY ERROR", "CHECK THE API KEY IN MENUCONFIG", banner_t::BAD, ERROR_BANNER_MS, false);
                break;
            case preview_state_t::INVALID:
                banner("REQUEST ERROR", "SEE THE SERIAL MONITOR", banner_t::BAD, ERROR_BANNER_MS, false);
                break;
            default:
                banner("SERVER ERROR", "CHECK WIFI AND TRY AGAIN", banner_t::BAD, ERROR_BANNER_MS, false);
                break;
        }
    }


    // handleYesButton(): log the scan, then say what the server did (the modal stays under the banner)
    void on_yes(SERVER_CLIENT &server)
    {
        if (!modal_open) {
            return;
        }
        modal_open = false;

        const scan_result_t r = server.send_scan(order.uid);
        char subtitle[64];
        snprintf(subtitle, sizeof(subtitle), "ORDER %s NOT LOGGED", order.item);

        switch (r.state) {
            case scan_state_t::RECORDED:
                ESP_LOGI(TAG, "YES pressed. Order Confirmed.");
                snprintf(subtitle, sizeof(subtitle), "ORDER %s LOGGED", order.item);
                banner("STATION CONFIRMED", subtitle, banner_t::GOOD, BANNER_MS, true);
                break;
            case scan_state_t::DUPLICATE:
                banner("ALREADY SCANNED", subtitle, banner_t::BAD, ERROR_BANNER_MS, true);
                break;
            case scan_state_t::FINISHED:
                banner("ROUTE COMPLETE", subtitle, banner_t::BAD, ERROR_BANNER_MS, true);
                break;
            case scan_state_t::UNASSIGNED:
                banner("TAG NOT ASSIGNED", subtitle, banner_t::BAD, ERROR_BANNER_MS, true);
                break;
            case scan_state_t::BAD_KEY:
                banner("DEVICE KEY ERROR", subtitle, banner_t::BAD, ERROR_BANNER_MS, true);
                break;
            case scan_state_t::INVALID:
                banner("REQUEST ERROR", subtitle, banner_t::BAD, ERROR_BANNER_MS, true);
                break;
            default:
                banner("SERVER ERROR", subtitle, banner_t::BAD, ERROR_BANNER_MS, true);
                break;
        }
    }


    // handleNoButton(): nothing goes to the server
    void on_no()
    {
        if (!modal_open) {
            return;
        }
        modal_open = false;
        ESP_LOGI(TAG, "NO pressed. Scan Rejected.");
        char subtitle[64];
        snprintf(subtitle, sizeof(subtitle), "ORDER %s FLAGGED", order.item);
        banner("SCAN REJECTED", subtitle, banner_t::BAD, BANNER_MS, true);
    }


    void scanner_task(void *)
    {
        SERVER_CLIENT server;
        if (gui) {
            gui->show_blank();
        }

        while (true) {
            cmd_t cmd;
            if (xQueueReceive(queue, &cmd, pdMS_TO_TICKS(TICK_MS)) == pdTRUE) {
                switch (cmd.type) {
                    case cmd_type_t::CARD:
                        on_card(server, cmd.uid);
                        break;
                    case cmd_type_t::NO_CARD:
                        if (!modal_open) {   // an open order stays up if a second read finds nothing
                            banner("NO CARD READ", "PRESS THE BUTTON AND TRY AGAIN", banner_t::BAD, ERROR_BANNER_MS, false);
                        }
                        break;
                    case cmd_type_t::YES:
                        on_yes(server);
                        break;
                    case cmd_type_t::NO:
                        on_no();
                        break;
                    case cmd_type_t::ENHANCE:
                        if (modal_open && gui) {
                            gui->toggle_enhanced();
                            ESP_LOGI(TAG, "Enhance toggled");
                        }
                        break;
                }
            }
            if (gui) {
                gui->tick();
            }
        }
    }
}


bool scanner_ui_start(SCANNER_DISPLAY *display)
{
    static SCANNER_GUI screen(display);   // static: the scanner task uses it forever
    gui = (display && screen.init()) ? &screen : nullptr;
    if (!gui) {
        ESP_LOGW(TAG, "no display, screens go to the serial monitor only");
    }

    queue = xQueueCreate(8, sizeof(cmd_t));
    if (!queue) {
        ESP_LOGE(TAG, "no memory for the scanner queue");
        return false;
    }

    // 10 KB: HTTP client + cJSON + the 1 KB preview reply, like the Setter task
    return xTaskCreate(scanner_task, "Scanner", 10240, nullptr, 4, nullptr) == pdPASS;
}


void scanner_ui_card(const char *uid) { send(cmd_type_t::CARD, uid); }
void scanner_ui_no_card()             { send(cmd_type_t::NO_CARD); }
void scanner_ui_yes()                 { send(cmd_type_t::YES); }
void scanner_ui_no()                  { send(cmd_type_t::NO); }
void scanner_ui_enhance()             { send(cmd_type_t::ENHANCE); }
