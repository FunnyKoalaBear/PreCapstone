#include <stdio.h>
#include <string.h>

#include <esp_log.h>
#include "esp_http_client.h"

#include "server_client.hpp"
#include "wifi.hpp"


namespace {
    static const char *TAG = "server_client";
}


//makes a JSON ouput for a scan read 
bool SERVER_CLIENT::send_scan(const char *uid)
{
    WIFI wifi;

    if (!wifi.connected()) {
        ESP_LOGW(TAG, "WiFi not connected, scan not sent");
        return false;
    }

    char body[64];
    snprintf(body, sizeof(body), "{\"tag_uid\":\"%s\"}", uid);

    return post_json(CONFIG_FACTORY_SERVER_SCAN_PATH, body);

}


bool SERVER_CLIENT::post_json(const char *path, const char *body)
{
    
    //Building server url 
    char url[128];
    snprintf(url, sizeof(url), "%s%s", CONFIG_FACTORY_SERVER_URL, path);
    //url - ip/path

    //Filling in server details 
    esp_http_client_config_t config = {};
    config.url = url;
    config.method = HTTP_METHOD_POST;
    config.timeout_ms = CONFIG_FACTORY_SERVER_TIMEOUT_MS;
    
    //setting up client to connect to server 
    esp_http_client_handle_t client = esp_http_client_init(&config);

    if (!client) {
        ESP_LOGE(TAG, "esp_http_client_init failed");
        return false;
    }

    //setting header for POST request 
    esp_http_client_set_header(client, "X-API-Key", CONFIG_FACTORY_API_KEY);

    //setting body for POST request 
    esp_http_client_set_post_field(client, body, strlen(body));

    //performing post 
    esp_err_t err = esp_http_client_perform(client);

    //checking status code 
    int status = 0;
    if (err == ESP_OK) {
        status = esp_http_client_get_status_code(client);
    } else {
        ESP_LOGW(TAG, "request failed: %s", esp_err_to_name(err));
    }

    //cleaning memory 
    esp_http_client_cleanup(client); 

    //return status 
    ESP_LOGI(TAG, "POST %s -> %d", url, status);
    return err == ESP_OK && status >= 200 && status < 300;
}
