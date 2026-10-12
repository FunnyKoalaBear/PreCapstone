#include <string.h>

#include <esp_log.h>
#include "esp_check.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "nvs_flash.h"

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

#include "wifi.hpp"


namespace {
    static const char *TAG = "wifi";

    constexpr EventBits_t CONNECTED_BIT = BIT0;

    EventGroupHandle_t s_events = nullptr;

    #define TRY(call)                                                                                \
        do {                                                                                         \
            esp_err_t err_ = (call);                                                                 \
            ESP_RETURN_ON_FALSE(err_ == ESP_OK, false, TAG, #call " failed: %s", esp_err_to_name(err_)); \
        } while (0)

    void on_event(void *, esp_event_base_t base, int32_t id, void *data)
    {
        if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
            esp_wifi_connect();
        } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
            auto *info = static_cast<wifi_event_sta_disconnected_t *>(data);
            xEventGroupClearBits(s_events, CONNECTED_BIT);
            ESP_LOGW(TAG, "disconnected (reason %d), retrying", info->reason);
            esp_wifi_connect();
        } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
            auto *got = static_cast<ip_event_got_ip_t *>(data);
            ESP_LOGI(TAG, "connected, IP " IPSTR, IP2STR(&got->ip_info.ip));
            xEventGroupSetBits(s_events, CONNECTED_BIT);
        }
    }
}


bool WIFI::init()
{
    ESP_RETURN_ON_FALSE(strlen(CONFIG_SCANNER_WIFI_SSID) > 0, false, TAG,
                        "no SSID set: run idf.py menuconfig, then Scanner WiFi");
    ESP_LOGI(TAG, "init WiFi, connecting to \"%s\"", CONFIG_SCANNER_WIFI_SSID);

    // WiFi stores its calibration data in NVS
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        TRY(nvs_flash_erase());
        err = nvs_flash_init();
    }
    TRY(err);

    s_events = xEventGroupCreate();
    ESP_RETURN_ON_FALSE(s_events, false, TAG, "out of memory");

    TRY(esp_netif_init());
    err = esp_event_loop_create_default();
    if (err != ESP_ERR_INVALID_STATE) {   // already created elsewhere is fine
        TRY(err);
    }
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
    TRY(esp_wifi_init(&init_cfg));

    TRY(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, nullptr));
    TRY(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, nullptr));

    wifi_config_t cfg = {};
    strlcpy(reinterpret_cast<char *>(cfg.sta.ssid), CONFIG_SCANNER_WIFI_SSID, sizeof(cfg.sta.ssid));
    strlcpy(reinterpret_cast<char *>(cfg.sta.password), CONFIG_SCANNER_WIFI_PASSWORD, sizeof(cfg.sta.password));
    cfg.sta.pmf_cfg.capable = true;
    cfg.sta.pmf_cfg.required = false;

    TRY(esp_wifi_set_mode(WIFI_MODE_STA));
    TRY(esp_wifi_set_config(WIFI_IF_STA, &cfg));
    TRY(esp_wifi_start());

    if (!wait_connected(CONFIG_SCANNER_WIFI_CONNECT_TIMEOUT_MS)) {
        ESP_LOGW(TAG, "not connected yet, still retrying in the background");
        return false;
    }
    return true;
}

bool WIFI::connected() const
{
    return s_events && (xEventGroupGetBits(s_events) & CONNECTED_BIT) != 0;
}

bool WIFI::wait_connected(uint32_t timeout_ms) const
{
    if (!s_events) {
        return false;
    }
    EventBits_t bits = xEventGroupWaitBits(s_events, CONNECTED_BIT, pdFALSE, pdFALSE, pdMS_TO_TICKS(timeout_ms));
    return (bits & CONNECTED_BIT) != 0;
}
