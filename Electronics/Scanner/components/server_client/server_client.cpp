#include <stdio.h>
#include <string.h>

#include <esp_log.h>
#include "esp_http_client.h"
#include "cJSON.h"

#include "server_client.hpp"
#include "wifi.hpp"


namespace {
    static const char *TAG = "server_client";

    void json_str(const cJSON *obj, const char *key, char *out, size_t out_size)
    {
        const char *s = cJSON_GetStringValue(cJSON_GetObjectItem(obj, key));
        strlcpy(out, s ? s : "", out_size);
    }

    int json_int(const cJSON *obj, const char *key)
    {
        const cJSON *v = cJSON_GetObjectItem(obj, key);
        return cJSON_IsNumber(v) ? v->valueint : 0;
    }
}


// GET /api/tag/<uid>/ and copy the reply into a tag_info_t
tag_info_t SERVER_CLIENT::lookup(const char *uid)
{
    tag_info_t info = {};

    char path[96];
    snprintf(path, sizeof(path), "%s%s/", CONFIG_FACTORY_SERVER_TAG_PATH, uid);

    char reply[1536];   // about 600 bytes; Japanese names take 6 bytes per character in JSON
    int status = request(path, nullptr, reply, sizeof(reply));
    cJSON *root = cJSON_Parse(reply);

    if (status == 200 && root) {
        char state[16];
        json_str(root, "state", state, sizeof(state));
        info.state = (strcmp(state, "finished") == 0) ? lookup_state_t::FINISHED : lookup_state_t::OPEN;
        json_str(root, "item", info.item, sizeof(info.item));
        json_str(root, "part", info.part, sizeof(info.part));
        json_str(root, "part_number", info.part_number, sizeof(info.part_number));
        json_str(root, "drawing", info.drawing, sizeof(info.drawing));
        json_str(root, "material", info.material, sizeof(info.material));
        json_str(root, "customer", info.customer, sizeof(info.customer));
        json_str(root, "order_number", info.order_number, sizeof(info.order_number));
        json_str(root, "ordered", info.ordered, sizeof(info.ordered));
        json_str(root, "due", info.due, sizeof(info.due));
        info.step = json_int(root, "step");
        info.steps = json_int(root, "steps");
        json_str(root, "process", info.process, sizeof(info.process));
        json_str(root, "event_type", info.event, sizeof(info.event));
        json_str(root, "box", info.box, sizeof(info.box));
    } else if (status == 404) {
        info.state = lookup_state_t::UNASSIGNED;
    } else if (status == 401) {
        info.state = lookup_state_t::BAD_KEY;
    } else {
        info.state = lookup_state_t::SERVER_ERROR;
    }

    cJSON_Delete(root);
    return info;
}


// POST /api/scan/ and turn the reply into a scan_result_t
scan_result_t SERVER_CLIENT::scan(const char *uid)
{
    scan_result_t result = {};

    char body[64];
    snprintf(body, sizeof(body), "{\"tag_uid\":\"%s\"}", uid);

    char reply[512];
    int status = request(CONFIG_FACTORY_SERVER_SCAN_PATH, body, reply, sizeof(reply));
    cJSON *root = cJSON_Parse(reply);

    if (status == 200) {
        char state[16];
        json_str(root, "state", state, sizeof(state));
        json_str(root, "item", result.item, sizeof(result.item));
        if (strcmp(state, "finished") == 0) {
            result.state = scan_state_t::FINISHED;
        } else if (strcmp(state, "duplicate") == 0) {
            result.state = scan_state_t::DUPLICATE;
        } else {
            result.state = scan_state_t::RECORDED;
            json_str(root, "process_name", result.process, sizeof(result.process));
            json_str(root, "event_type", result.event, sizeof(result.event));
        }
    } else if (status == 404) {
        result.state = scan_state_t::UNASSIGNED;
    } else if (status == 401) {
        result.state = scan_state_t::BAD_KEY;
    } else if (status == 400) {
        result.state = scan_state_t::INVALID;
    } else {
        result.state = scan_state_t::SERVER_ERROR;
    }

    cJSON_Delete(root);   // cJSON allocates every node: always free the tree
    return result;
}


bool SERVER_CLIENT::send_scan(const char *uid)
{
    scan_state_t state = scan(uid).state;
    return state == scan_state_t::RECORDED || state == scan_state_t::FINISHED || state == scan_state_t::DUPLICATE;
}


int SERVER_CLIENT::request(const char *path, const char *body, char *reply, size_t reply_size)
{
    reply[0] = '\0';

    WIFI wifi;
    if (!wifi.connected()) {
        ESP_LOGW(TAG, "WiFi not connected, request not sent");
        return -1;
    }

    //Building server url: base + path
    char url[128];
    snprintf(url, sizeof(url), "%s%s", CONFIG_FACTORY_SERVER_URL, path);

    esp_http_client_config_t config = {};
    config.url = url;
    config.method = body ? HTTP_METHOD_POST : HTTP_METHOD_GET;
    config.timeout_ms = CONFIG_FACTORY_SERVER_TIMEOUT_MS;

    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        ESP_LOGE(TAG, "esp_http_client_init failed");
        return -1;
    }

    esp_http_client_set_header(client, "X-API-Key", CONFIG_FACTORY_API_KEY);
    if (body) {
        esp_http_client_set_header(client, "Content-Type", "application/json");
    }

    // open -> write body -> read headers -> read body (perform() can't hand the body back to us)
    const int body_len = body ? strlen(body) : 0;
    int status = -1;
    esp_err_t err = esp_http_client_open(client, body_len);

    if (err == ESP_OK && body_len > 0 && esp_http_client_write(client, body, body_len) != body_len) {
        err = ESP_FAIL;
    }

    if (err == ESP_OK && esp_http_client_fetch_headers(client) >= 0) {
        status = esp_http_client_get_status_code(client);
        int n = esp_http_client_read_response(client, reply, reply_size - 1);
        reply[n > 0 ? n : 0] = '\0';
        if (n >= (int)reply_size - 1) {
            ESP_LOGW(TAG, "reply cut at %u bytes", (unsigned)reply_size - 1);
        }
    } else {
        ESP_LOGW(TAG, "request failed: %s", err == ESP_OK ? "no reply" : esp_err_to_name(err));
    }

    esp_http_client_close(client);
    esp_http_client_cleanup(client);

    ESP_LOGI(TAG, "%s %s -> %d", body ? "POST" : "GET", url, status);
    return status;
}
