#include <stdio.h>
#include <string.h>
#include <strings.h>

#include <esp_log.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "setter_app.hpp"
#include "server_client.hpp"
#include "rfid.hpp"
#include "buzzer.hpp"


namespace {
    static const char *TAG = "setter_app";

    constexpr int PAGE_SIZE = 8;              // rows per screen (also fits 8 panel buttons)
    constexpr int CARD_TIMEOUT_MS = 15000;    // how long to wait for a card after a pick
    constexpr int RESULT_SHOW_MS = 3000;      // how long the result stays before the list comes back
    constexpr int ERROR_SHOW_MS = 1500;

    enum class cmd_type_t { RELOAD, NEXT, PREV, PICK, PICK_CODE, CANCEL };

    struct cmd_t {
        cmd_type_t type;
        int row;
        char code[24];
    };

    enum class card_t { READ, TIMEOUT, CANCELLED };

    QueueHandle_t queue = nullptr;
    SETTER_UI *ui = nullptr;
    RFID *rfid = nullptr;
    BUZZER *buzzer = nullptr;


    void send(cmd_type_t type, int row = 0, const char *code = nullptr)
    {
        if (!queue) {
            return;
        }
        cmd_t cmd = {};
        cmd.type = type;
        cmd.row = row;
        if (code) {
            strlcpy(cmd.code, code, sizeof(cmd.code));
        }
        xQueueSend(queue, &cmd, 0);   // never block the caller (UI callback, keyboard task)
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


    // Waits for a card while still listening for cancel; writes the UID as hex, e.g. "0D11696B"
    card_t wait_for_card(char *uid_hex, size_t size)
    {
        const TickType_t start = xTaskGetTickCount();

        while ((xTaskGetTickCount() - start) < pdMS_TO_TICKS(CARD_TIMEOUT_MS)) {
            cmd_t cmd;
            if (xQueueReceive(queue, &cmd, 0) == pdTRUE &&
                (cmd.type == cmd_type_t::CANCEL || cmd.type == cmd_type_t::RELOAD)) {
                return card_t::CANCELLED;
            }

            uint8_t uid[10];
            uint8_t uid_length = 0;
            if (rfid->poll_uid(uid, &uid_length)) {
                uid_hex[0] = '\0';
                for (uint8_t i = 0; i < uid_length && 2 * i + 2 < size; i++) {
                    snprintf(uid_hex + 2 * i, size - 2 * i, "%02X", uid[i]);
                }
                return card_t::READ;
            }
            vTaskDelay(pdMS_TO_TICKS(50));
        }
        return card_t::TIMEOUT;
    }


    // Pick -> card -> POST /api/assign/ -> show the answer
    void link_item(SERVER_CLIENT &server, const setter_item_t &item)
    {
        ui->show_waiting_for_card(item, CARD_TIMEOUT_MS / 1000);
        beep(1, 60);

        char uid[24];
        card_t card = wait_for_card(uid, sizeof(uid));
        if (card != card_t::READ) {
            ui->show_error(card == card_t::TIMEOUT ? "No card read" : "Cancelled");
            vTaskDelay(pdMS_TO_TICKS(ERROR_SHOW_MS));
            return;
        }

        ESP_LOGI(TAG, "linking tag %s to item %s (id %d)", uid, item.code, item.id);
        beep(1, 60);
        ui->show_message("Linking...");

        assign_result_t result = server.assign(uid, item.id);
        ui->show_result(item, result);

        if (result.state == assign_state_t::ASSIGNED) {
            beep(2, 100);
        } else {
            beep(1, 600);
        }
        vTaskDelay(pdMS_TO_TICKS(RESULT_SHOW_MS));
        xQueueReset(queue);   // ignore keys pressed while the result was showing
    }


    // Looks through every page for an item code typed on the keyboard
    bool find_item(SERVER_CLIENT &server, const char *code, setter_item_t &found)
    {
        setter_item_t page[PAGE_SIZE];
        int count = 0;
        int total = 0;

        for (int offset = 0; ; offset += PAGE_SIZE) {
            if (!server.fetch_items(offset, PAGE_SIZE, page, PAGE_SIZE, &count, &total)) {
                return false;
            }
            for (int i = 0; i < count; i++) {
                if (strcasecmp(page[i].code, code) == 0) {
                    found = page[i];
                    return true;
                }
            }
            if (count == 0 || offset + count >= total) {
                return false;
            }
        }
    }


    void setter_task(void *argument)
    {
        SERVER_CLIENT server;
        setter_item_t items[PAGE_SIZE];
        int count = 0;
        int total = 0;
        int offset = 0;
        bool need_load = true;

        while (true) {
            if (need_load) {
                ui->show_message("Loading items...");
                if (!server.fetch_items(offset, PAGE_SIZE, items, PAGE_SIZE, &count, &total)) {
                    count = 0;
                    ui->show_error("Server error. Press r to retry");
                } else if (count == 0 && offset > 0) {
                    offset = 0;   // the list got shorter: go back to page 1
                    continue;
                } else {
                    ui->show_list(items, count, offset, total);
                }
                need_load = false;
            }

            cmd_t cmd;
            xQueueReceive(queue, &cmd, portMAX_DELAY);

            switch (cmd.type) {
                case cmd_type_t::RELOAD:
                case cmd_type_t::CANCEL:
                    need_load = true;
                    break;

                case cmd_type_t::NEXT:
                    if (offset + count < total) {
                        offset += PAGE_SIZE;
                        need_load = true;
                    }
                    break;

                case cmd_type_t::PREV:
                    if (offset > 0) {
                        offset = offset > PAGE_SIZE ? offset - PAGE_SIZE : 0;
                        need_load = true;
                    }
                    break;

                case cmd_type_t::PICK:
                    if (cmd.row >= 0 && cmd.row < count) {
                        link_item(server, items[cmd.row]);
                    } else {
                        ui->show_error("No such row");
                        vTaskDelay(pdMS_TO_TICKS(ERROR_SHOW_MS));
                    }
                    need_load = true;   // the item may be full now, reload
                    break;

                case cmd_type_t::PICK_CODE: {
                    setter_item_t item;
                    ui->show_message("Searching...");
                    if (find_item(server, cmd.code, item)) {
                        link_item(server, item);
                    } else {
                        ui->show_error("Item not found or already tagged");
                        vTaskDelay(pdMS_TO_TICKS(ERROR_SHOW_MS));
                    }
                    need_load = true;
                    break;
                }
            }
        }
    }
}


bool setter_app_start(SETTER_UI *setter_ui, RFID *setter_rfid, BUZZER *setter_buzzer)
{
    ui = setter_ui;
    rfid = setter_rfid;
    buzzer = setter_buzzer;

    queue = xQueueCreate(8, sizeof(cmd_t));
    if (!queue) {
        ESP_LOGE(TAG, "no memory for the command queue");
        return false;
    }

    // 10 KB: HTTP client + cJSON + two pages of items
    return xTaskCreate(setter_task, "Setter", 10240, nullptr, 5, nullptr) == pdPASS;
}


void setter_reload()                    { send(cmd_type_t::RELOAD); }
void setter_next_page()                 { send(cmd_type_t::NEXT); }
void setter_prev_page()                 { send(cmd_type_t::PREV); }
void setter_pick(int row)               { send(cmd_type_t::PICK, row); }
void setter_pick_code(const char *code) { send(cmd_type_t::PICK_CODE, 0, code); }
void setter_cancel()                    { send(cmd_type_t::CANCEL); }
