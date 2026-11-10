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


// POST {"tag_uid": "..."} and turn the reply into a scan_state_t
scan_result_t SERVER_CLIENT::send_scan(const char *uid)
{
    scan_result_t result = {};
    strlcpy(result.uid, uid, sizeof(result.uid));

    char body[64];
    snprintf(body, sizeof(body), "{\"tag_uid\":\"%s\"}", uid);

    char reply[512];   // a scan reply is about 250 bytes
    const int status = request(true, CONFIG_FACTORY_SERVER_SCAN_PATH, body, reply, sizeof(reply));
    cJSON *root = cJSON_Parse(reply);   // nullptr if the body is empty or not JSON

    if (status >= 200 && status < 300) {
        const char *state = cJSON_GetStringValue(cJSON_GetObjectItem(root, "state"));
        if (state && strcmp(state, "finished") == 0) {
            result.state = scan_state_t::FINISHED;
        } else if (state && strcmp(state, "duplicate") == 0) {
            result.state = scan_state_t::DUPLICATE;
        } else {
            result.state = scan_state_t::RECORDED;
        }
        json_str(root, "item", result.item, sizeof(result.item));
        json_str(root, "process_name", result.process, sizeof(result.process));
        json_str(root, "event_type", result.event_type, sizeof(result.event_type));
        json_str(root, "status", result.status, sizeof(result.status));
    } else if (status == 404) {
        result.state = scan_state_t::UNASSIGNED;
    } else if (status == 401) {
        result.state = scan_state_t::BAD_KEY;
    } else if (status == 400 || status == 405) {
        result.state = scan_state_t::INVALID;
    } else {
        result.state = scan_state_t::SERVER_ERROR;
    }

    cJSON_Delete(root);   // cJSON allocates every node: always free the tree
    return result;
}


// GET /api/scan-preview/?tag_uid=.. and copy the order and next step out of the reply
scan_preview_t SERVER_CLIENT::preview(const char *uid)
{
    scan_preview_t p = {};
    strlcpy(p.uid, uid, sizeof(p.uid));

    char path[96];
    snprintf(path, sizeof(path), "%s?tag_uid=%s", CONFIG_FACTORY_SERVER_PREVIEW_PATH, uid);   // hex UID: no escaping needed

    char reply[1024];   // about 500 bytes, more with Japanese names
    const int status = request(false, path, nullptr, reply, sizeof(reply));
    cJSON *root = cJSON_Parse(reply);
    const char *state = cJSON_GetStringValue(cJSON_GetObjectItem(root, "state"));
    const char *error = cJSON_GetStringValue(cJSON_GetObjectItem(root, "error"));

    if (status == 200 && state) {
        p.state = strcmp(state, "finished") == 0 ? preview_state_t::FINISHED : preview_state_t::NEXT;
        json_str(root, "item", p.item, sizeof(p.item));
        json_str(root, "part", p.part, sizeof(p.part));
        json_str(root, "drawing_number", p.drawing, sizeof(p.drawing));
        json_str(root, "revision", p.revision, sizeof(p.revision));
        json_str(root, "material", p.material, sizeof(p.material));
        json_str(root, "customer", p.customer, sizeof(p.customer));
        json_str(root, "order_number", p.order_number, sizeof(p.order_number));
        json_str(root, "ordered", p.ordered, sizeof(p.ordered));
        json_str(root, "due", p.due, sizeof(p.due));
        json_str(root, "status", p.status, sizeof(p.status));
        json_str(root, "process_name", p.process, sizeof(p.process));
        json_str(root, "event_type", p.event_type, sizeof(p.event_type));
        p.quantity = json_int(root, "quantity");
        p.step = json_int(root, "step");
        p.steps = json_int(root, "steps");
    } else if (status == 404 && error && strcmp(error, "unassigned_tag") == 0) {
        p.state = preview_state_t::UNASSIGNED;
    } else if (status == 401) {
        p.state = preview_state_t::BAD_KEY;
    } else if (status == 400 || status == 404 || status == 405) {
        if (status == 404) {
            ESP_LOGW(TAG, "no %s on the server: update the factory server", CONFIG_FACTORY_SERVER_PREVIEW_PATH);
        }
        p.state = preview_state_t::INVALID;
    } else {
        p.state = preview_state_t::SERVER_ERROR;
    }

    cJSON_Delete(root);
    return p;
}


int SERVER_CLIENT::request(bool post, const char *path, const char *body, char *reply, size_t reply_size)
{
    reply[0] = '\0';

    WIFI wifi;
    if (!wifi.connected()) {
        ESP_LOGW(TAG, "WiFi not connected, request not sent");
        return -1;
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
        return -1;
    }

    esp_http_client_set_header(client, "X-API-Key", CONFIG_FACTORY_API_KEY);
    esp_http_client_set_header(client, "Cookie", "django_language=en");   // English "status" text
    if (post) {
        esp_http_client_set_header(client, "Content-Type", "application/json");
    }

    // open -> write body -> read headers -> read body (perform() can't hand the body back to us)
    const int body_len = post ? strlen(body) : 0;
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

    //cleaning memory
    esp_http_client_close(client);
    esp_http_client_cleanup(client);

    ESP_LOGI(TAG, "%s %s -> %d", post ? "POST" : "GET", url, status);
    return status;
}
