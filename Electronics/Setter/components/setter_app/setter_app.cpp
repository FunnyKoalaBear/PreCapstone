#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <algorithm>

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

    constexpr int MAX_ITEMS = 100;            // items kept on the device (about 20 KB)
    constexpr int FETCH_PAGE = 20;            // items per HTTP request
    constexpr int JUMP = 8;                   // n / p move this many items
    constexpr int CARD_TIMEOUT_MS = 15000;    // how long to wait for a card after a pick
    constexpr int RESULT_SHOW_MS = 3000;      // how long the result stays before the list comes back
    constexpr int ERROR_SHOW_MS = 1500;
    constexpr int TICK_MS = 1000;             // ui->tick() while idle (clock)

    enum class cmd_type_t {
        RELOAD, UP, DOWN, PAGE_UP, PAGE_DOWN, LINK, BACK, DETAILS, DRAWING, VIEW, SORT, PICK, PICK_CODE,
    };

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

    // list state; only the setter task touches it
    setter_item_t items[MAX_ITEMS];
    int count = 0;
    int total = 0;
    int selected = 0;
    int version = 0;
    setter_view_t view = setter_view_t::SQUARES;
    setter_sort_t sort_by = setter_sort_t::NUMBER;
    setter_modal_t modal = setter_modal_t::NONE;
    char banner[48] = "";


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
        xQueueSend(queue, &cmd, 0);   // never block the caller (button task, keyboard task)
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


    void show_list()
    {
        setter_list_t list = {};
        list.items = items;
        list.count = count;
        list.total = total;
        list.selected = selected;
        list.version = version;
        list.view = view;
        list.sort = sort_by;
        list.modal = modal;
        list.banner = banner[0] ? banner : nullptr;
        ui->show_list(list);
        banner[0] = '\0';   // a banner is shown once
    }


    // Sorts by the current filter and keeps the same item selected
    void sort_items()
    {
        const int keep_id = (count > 0) ? items[selected].id : -1;

        std::sort(items, items + count, [](const setter_item_t &a, const setter_item_t &b) {
            int c = 0;
            switch (sort_by) {
                case setter_sort_t::START: c = strcmp(a.ordered, b.ordered); break;
                case setter_sort_t::DUE:   c = strcmp(a.due, b.due); break;
                case setter_sort_t::PART:  c = strcmp(a.part, b.part); break;
                default: break;
            }
            return c != 0 ? c < 0 : strcmp(a.code, b.code) < 0;
        });

        for (int i = 0; i < count; i++) {
            if (items[i].id == keep_id) {
                selected = i;
            }
        }
        version++;
    }


    // Loads every item that needs a tag, FETCH_PAGE at a time (up to MAX_ITEMS)
    bool load_all(SERVER_CLIENT &server)
    {
        const int keep_id = (count > 0) ? items[selected].id : -1;
        const int keep_index = selected;
        int loaded = 0;
        int server_total = 0;

        while (loaded < MAX_ITEMS) {
            int got = 0;
            int limit = std::min(FETCH_PAGE, MAX_ITEMS - loaded);
            if (!server.fetch_items(loaded, limit, items + loaded, limit, &got, &server_total)) {
                return false;
            }
            loaded += got;
            if (got == 0 || loaded >= server_total) {
                break;
            }
        }

        count = loaded;
        total = server_total;
        selected = std::min(keep_index, std::max(count - 1, 0));   // same place if the item is gone
        for (int i = 0; i < count; i++) {
            if (items[i].id == keep_id) {
                selected = i;
            }
        }
        sort_items();
        if (modal != setter_modal_t::NONE && count == 0) {
            modal = setter_modal_t::NONE;
        }
        return true;
    }


    // Waits for a card while still listening for cancel; writes the UID as hex, e.g. "0D11696B"
    card_t wait_for_card(char *uid_hex, size_t size)
    {
        const TickType_t start = xTaskGetTickCount();

        while ((xTaskGetTickCount() - start) < pdMS_TO_TICKS(CARD_TIMEOUT_MS)) {
            cmd_t cmd;
            if (xQueueReceive(queue, &cmd, 0) == pdTRUE &&
                (cmd.type == cmd_type_t::BACK || cmd.type == cmd_type_t::RELOAD)) {
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
            snprintf(banner, sizeof(banner), "%s LINKED", item.code);
        } else {
            beep(1, 600);
        }
        vTaskDelay(pdMS_TO_TICKS(RESULT_SHOW_MS));
        xQueueReset(queue);   // ignore keys pressed while the result was showing
    }


    int find_code(const char *code)
    {
        for (int i = 0; i < count; i++) {
            if (strcasecmp(items[i].code, code) == 0) {
                return i;
            }
        }
        return -1;
    }


    void setter_task(void *argument)
    {
        SERVER_CLIENT server;
        bool need_load = true;

        while (true) {
            if (need_load) {
                ui->show_message("Loading items...");
                if (load_all(server)) {
                    show_list();
                } else {
                    count = 0;
                    ui->show_error("Server error. Press back to retry");
                }
                need_load = false;
            }

            cmd_t cmd;
            if (xQueueReceive(queue, &cmd, pdMS_TO_TICKS(TICK_MS)) != pdTRUE) {
                ui->tick();
                continue;
            }

            const bool browsing = count > 0 && modal == setter_modal_t::NONE;

            switch (cmd.type) {
                case cmd_type_t::RELOAD:
                    need_load = true;
                    break;

                case cmd_type_t::BACK:
                    if (modal != setter_modal_t::NONE) {
                        modal = setter_modal_t::NONE;
                        show_list();
                    } else {
                        need_load = true;
                    }
                    break;

                case cmd_type_t::UP:
                case cmd_type_t::DOWN:
                    if (browsing) {
                        int step = (cmd.type == cmd_type_t::UP) ? -1 : 1;
                        selected = (selected + step + count) % count;
                        show_list();
                    }
                    break;

                case cmd_type_t::PAGE_UP:
                case cmd_type_t::PAGE_DOWN:
                    if (browsing) {
                        selected = (cmd.type == cmd_type_t::PAGE_UP) ? std::max(selected - JUMP, 0)
                                                                     : std::min(selected + JUMP, count - 1);
                        show_list();
                    }
                    break;

                case cmd_type_t::DETAILS:
                    if (count > 0) {
                        modal = (modal == setter_modal_t::NONE) ? setter_modal_t::DETAILS : setter_modal_t::NONE;
                        show_list();
                    }
                    break;

                case cmd_type_t::DRAWING:
                    if (count > 0) {
                        modal = (modal == setter_modal_t::DRAWING) ? setter_modal_t::DETAILS : setter_modal_t::DRAWING;
                        show_list();
                    }
                    break;

                case cmd_type_t::VIEW:
                    view = static_cast<setter_view_t>((static_cast<int>(view) + 1) % 3);
                    if (count > 0) {
                        show_list();
                    }
                    break;

                case cmd_type_t::SORT:
                    sort_by = static_cast<setter_sort_t>((static_cast<int>(sort_by) + 1) % 4);
                    sort_items();
                    if (count > 0) {
                        show_list();
                    }
                    break;

                case cmd_type_t::LINK:
                    if (count > 0) {
                        modal = setter_modal_t::NONE;
                        link_item(server, items[selected]);
                        need_load = true;   // the item may be full now
                    }
                    break;

                case cmd_type_t::PICK:
                    if (cmd.row >= 0 && cmd.row < count) {
                        selected = cmd.row;
                        modal = setter_modal_t::NONE;
                        link_item(server, items[cmd.row]);
                    } else {
                        ui->show_error("No such row");
                        vTaskDelay(pdMS_TO_TICKS(ERROR_SHOW_MS));
                    }
                    need_load = true;
                    break;

                case cmd_type_t::PICK_CODE: {
                    int i = find_code(cmd.code);
                    if (i < 0 && load_all(server)) {
                        i = find_code(cmd.code);   // maybe new since the last load
                    }
                    if (i >= 0) {
                        selected = i;
                        modal = setter_modal_t::NONE;
                        link_item(server, items[i]);
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

    // 10 KB: HTTP client + cJSON (the item list itself is a static array)
    return xTaskCreate(setter_task, "Setter", 10240, nullptr, 5, nullptr) == pdPASS;
}


void setter_up()                        { send(cmd_type_t::UP); }
void setter_down()                      { send(cmd_type_t::DOWN); }
void setter_prev_page()                 { send(cmd_type_t::PAGE_UP); }
void setter_next_page()                 { send(cmd_type_t::PAGE_DOWN); }
void setter_link_selected()             { send(cmd_type_t::LINK); }
void setter_back()                      { send(cmd_type_t::BACK); }
void setter_toggle_details()            { send(cmd_type_t::DETAILS); }
void setter_toggle_drawing()            { send(cmd_type_t::DRAWING); }
void setter_next_view()                 { send(cmd_type_t::VIEW); }
void setter_next_sort()                 { send(cmd_type_t::SORT); }
void setter_pick(int row)               { send(cmd_type_t::PICK, row); }
void setter_pick_code(const char *code) { send(cmd_type_t::PICK_CODE, 0, code); }
void setter_reload()                    { send(cmd_type_t::RELOAD); }
