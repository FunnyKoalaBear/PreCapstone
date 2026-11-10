#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <algorithm>

#include <esp_log.h>
#include "esp_http_client.h"
#include "cJSON.h"

#include "server_client.hpp"
#include "wifi.hpp"


namespace {
    static const char *TAG = "server_client";

    const char *language_cookie = "django_language=en";

    // a 20-item page is about 5 KB; on the heap so it never overflows a task stack
    constexpr size_t LIST_REPLY_SIZE = 8192;

    int json_int(const cJSON *obj, const char *key)
    {
        const cJSON *v = cJSON_GetObjectItem(obj, key);
        return cJSON_IsNumber(v) ? v->valueint : 0;
    }

    void json_str(const cJSON *obj, const char *key, char *out, size_t out_size)
    {
        const char *s = cJSON_GetStringValue(cJSON_GetObjectItem(obj, key));
        strlcpy(out, s ? s : "", out_size);
    }
}


void SERVER_CLIENT::set_language(bool japanese)
{
    language_cookie = japanese ? "django_language=ja" : "django_language=en";
}


//makes a JSON ouput for a scan read
bool SERVER_CLIENT::send_scan(const char *uid)
{
    char body[64];
    snprintf(body, sizeof(body), "{\"tag_uid\":\"%s\"}", uid);

    char reply[256];
    int status = request(true, CONFIG_FACTORY_SERVER_SCAN_PATH, body, reply, sizeof(reply));
    return status >= 200 && status < 300;
}


// GET /api/assignable-items/?offset=..&limit=.. and copy the rows into `items`
bool SERVER_CLIENT::fetch_items(int offset, int limit, setter_item_t *items, int max_items, int *count, int *total)
{
    *count = 0;
    *total = 0;

    char path[96];
    snprintf(path, sizeof(path), "%s?offset=%d&limit=%d", CONFIG_FACTORY_SERVER_ITEMS_PATH, offset, limit);

    char *reply = static_cast<char *>(malloc(LIST_REPLY_SIZE));
    if (!reply) {
        ESP_LOGE(TAG, "no memory for the item list");
        return false;
    }

    bool ok = false;
    int status = request(false, path, nullptr, reply, LIST_REPLY_SIZE);

    if (status == 200) {
        cJSON *root = cJSON_Parse(reply);
        const cJSON *list = cJSON_GetObjectItem(root, "items");

        if (cJSON_IsArray(list)) {
            *total = json_int(root, "total");

            const cJSON *row;
            cJSON_ArrayForEach(row, list) {
                if (*count >= max_items) {
                    break;
                }
                setter_item_t &item = items[*count];
                item.id = json_int(row, "id");
                json_str(row, "item", item.code, sizeof(item.code));
                json_str(row, "part", item.part, sizeof(item.part));
                json_str(row, "customer", item.customer, sizeof(item.customer));
                json_str(row, "order_number", item.order_number, sizeof(item.order_number));
                json_str(row, "due", item.due, sizeof(item.due));
                json_str(row, "ordered", item.ordered, sizeof(item.ordered));
                item.linked = json_int(row, "linked");
                item.boxes = json_int(row, "boxes");
                item.quantity = json_int(row, "quantity");
                item.steps = json_int(row, "steps");
                (*count)++;
            }
            ok = true;
        } else {
            ESP_LOGW(TAG, "item list reply is not the expected JSON");
        }

        cJSON_Delete(root);   // cJSON allocates every node: always free the tree
    }

    free(reply);
    return ok;
}


// POST /api/assign/ and turn the HTTP status into an assign_state_t
assign_result_t SERVER_CLIENT::assign(const char *uid, int item_id, bool replace)
{
    assign_result_t result = {};

    char body[112];
    snprintf(body, sizeof(body), "{\"tag_uid\":\"%s\",\"item\":%d%s}", uid, item_id, replace ? ",\"replace\":true" : "");

    char reply[512];
    int status = request(true, CONFIG_FACTORY_SERVER_ASSIGN_PATH, body, reply, sizeof(reply));
    cJSON *root = cJSON_Parse(reply);

    switch (status) {
        case 200:
            result.state = assign_state_t::ASSIGNED;
            result.linked = json_int(root, "linked");
            result.boxes = json_int(root, "boxes");
            strlcpy(result.message, "Assigned, printing", sizeof(result.message));
            json_str(root, "released", result.released, sizeof(result.released));
            break;
        case 409: {
            result.state = assign_state_t::REFUSED;
            json_str(root, "detail", result.message, sizeof(result.message));
            char reason[24];
            json_str(root, "reason", reason, sizeof(reason));
            result.tag_in_use = strcmp(reason, "tag_in_use") == 0;
            json_str(root, "current_item", result.current_item, sizeof(result.current_item));
            break;
        }
        case 401:
            result.state = assign_state_t::BAD_KEY;
            strlcpy(result.message, "Device key error", sizeof(result.message));
            break;
        case 400:
            result.state = assign_state_t::INVALID;
            strlcpy(result.message, "Request error", sizeof(result.message));
            break;
        default:
            result.state = assign_state_t::SERVER_ERROR;
            strlcpy(result.message, "Server error", sizeof(result.message));
            break;
    }

    cJSON_Delete(root);
    return result;
}


int SERVER_CLIENT::request(bool post, const char *path, const char *body, char *reply, size_t reply_size)
{
    reply[0] = '\0';

    int status = -1;
    esp_http_client_handle_t client = open(post, path, body, &status);

    if (status > 0) {
        int n = esp_http_client_read_response(client, reply, reply_size - 1);
        reply[n > 0 ? n : 0] = '\0';
        if (n >= (int)reply_size - 1) {
            ESP_LOGW(TAG, "reply cut at %u bytes", (unsigned)reply_size - 1);
        }
    }

    finish(client);
    return status;
}


// GET /api/items/<id>/drawing-pixels/?w=..&h=.. and hand the pixels to the sink as they arrive
drawing_state_t SERVER_CLIENT::fetch_drawing(int item_id, const picture_sink_t &sink)
{
    char path[96];
    snprintf(path, sizeof(path), CONFIG_FACTORY_SERVER_DRAWING_PATH "?w=%d&h=%d", item_id, sink.width, sink.height);

    int status = -1;
    esp_http_client_handle_t client = open(false, path, nullptr, &status);

    drawing_state_t state = drawing_state_t::FAILED;
    switch (status) {
        case 200: state = read_picture(client, sink); break;
        case 404: state = drawing_state_t::NO_DRAWING; break;
        case 422: state = drawing_state_t::UNREADABLE; break;
        case 401: state = drawing_state_t::BAD_KEY; break;
        default: break;
    }

    finish(client);
    return state;
}


// Fills the sink's buffer with whole rows straight from the socket, then draws them: never more than one band in RAM
drawing_state_t SERVER_CLIENT::read_picture(esp_http_client_handle_t client, const picture_sink_t &sink)
{
    const int row_bytes = sink.width * 2;
    const int64_t expected = (int64_t)row_bytes * sink.height;
    const int64_t length = esp_http_client_get_content_length(client);
    if (length > 0 && length != expected) {
        ESP_LOGW(TAG, "drawing is %lld bytes, expected %lld", (long long)length, (long long)expected);
        return drawing_state_t::FAILED;
    }

    char *buffer = reinterpret_cast<char *>(sink.buffer);
    int y = 0;
    while (y < sink.height) {
        const int rows = std::min(sink.buffer_rows, sink.height - y);
        const int want = rows * row_bytes;
        int got = 0;
        while (got < want) {
            int n = esp_http_client_read(client, buffer + got, want - got);
            if (n <= 0) {
                ESP_LOGW(TAG, "drawing cut short at row %d", y);
                return drawing_state_t::FAILED;
            }
            got += n;
        }
        if (!sink.draw_rows(sink.ctx, y, rows, sink.buffer)) {
            return drawing_state_t::FAILED;
        }
        y += rows;
    }
    return drawing_state_t::SHOWN;
}


esp_http_client_handle_t SERVER_CLIENT::open(bool post, const char *path, const char *body, int *status)
{
    *status = -1;

    WIFI wifi;
    if (!wifi.connected()) {
        ESP_LOGW(TAG, "WiFi not connected, request not sent");
        return nullptr;
    }

    //Building server url: base + path
    char url[192];
    snprintf(url, sizeof(url), "%s%s", CONFIG_FACTORY_SERVER_URL, path);

    //Filling in server details
    esp_http_client_config_t config = {};
    config.url = url;
    config.method = post ? HTTP_METHOD_POST : HTTP_METHOD_GET;
    config.timeout_ms = CONFIG_FACTORY_SERVER_TIMEOUT_MS;

    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        ESP_LOGE(TAG, "esp_http_client_init failed");
        return nullptr;
    }

    esp_http_client_set_header(client, "X-API-Key", CONFIG_FACTORY_API_KEY);
    esp_http_client_set_header(client, "Cookie", language_cookie);   // language of the "detail" text
    esp_http_client_set_header(client, "Accept-Encoding", "identity");   // never gzip: the pixels go straight to the panel
    if (post) {
        esp_http_client_set_header(client, "Content-Type", "application/json");
    }

    // open -> write body -> read headers; the caller reads the body (perform() can't hand it back to us)
    int body_len = post ? strlen(body) : 0;
    esp_err_t err = esp_http_client_open(client, body_len);

    if (err == ESP_OK && body_len > 0 && esp_http_client_write(client, body, body_len) != body_len) {
        err = ESP_FAIL;
    }

    if (err == ESP_OK && esp_http_client_fetch_headers(client) >= 0) {
        *status = esp_http_client_get_status_code(client);
    } else {
        ESP_LOGW(TAG, "request failed: %s", err == ESP_OK ? "no reply" : esp_err_to_name(err));
    }

    ESP_LOGI(TAG, "%s %s -> %d", post ? "POST" : "GET", url, *status);
    return client;
}


//cleaning memory
void SERVER_CLIENT::finish(esp_http_client_handle_t client)
{
    if (client) {
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
    }
}
