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
}


// POST /api/scan/ and turn the reply into a scan_result_t
scan_result_t SERVER_CLIENT::scan(const char *uid)
{
    scan_result_t result = {};

    char body[64];
    snprintf(body, sizeof(body), "{\"tag_uid\":\"%s\"}", uid);

    char reply[512];
    int status = post_json(CONFIG_FACTORY_SERVER_SCAN_PATH, body, reply, sizeof(reply));
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


int SERVER_CLIENT::post_json(const char *path, const char *body, char *reply, size_t reply_size)
{
    reply[0] = '\0';

    WIFI wifi;
    if (!wifi.connected()) {
        ESP_LOGW(TAG, "WiFi not connected, scan not sent");
        return -1;
    }

    //Building server url: base + path
    char url[128];
    snprintf(url, sizeof(url), "%s%s", CONFIG_FACTORY_SERVER_URL, path);

    esp_http_client_config_t config = {};
    config.url = url;
    config.method = HTTP_METHOD_POST;
    config.timeout_ms = CONFIG_FACTORY_SERVER_TIMEOUT_MS;

    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        ESP_LOGE(TAG, "esp_http_client_init failed");
        return -1;
    }

    esp_http_client_set_header(client, "X-API-Key", CONFIG_FACTORY_API_KEY);
    esp_http_client_set_header(client, "Content-Type", "application/json");

    // open -> write body -> read headers -> read body (perform() can't hand the body back to us)
    const int body_len = strlen(body);
    int status = -1;
    esp_err_t err = esp_http_client_open(client, body_len);

    if (err == ESP_OK && esp_http_client_write(client, body, body_len) != body_len) {
        err = ESP_FAIL;
    }

    if (err == ESP_OK && esp_http_client_fetch_headers(client) >= 0) {
        status = esp_http_client_get_status_code(client);
        int n = esp_http_client_read_response(client, reply, reply_size - 1);
        reply[n > 0 ? n : 0] = '\0';
    } else {
        ESP_LOGW(TAG, "request failed: %s", err == ESP_OK ? "no reply" : esp_err_to_name(err));
    }

    esp_http_client_close(client);
    esp_http_client_cleanup(client);

    ESP_LOGI(TAG, "POST %s -> %d", url, status);
    return status;
}
