#include <esp_log.h>
#include "driver/gpio.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "pn532_driver_i2c.h"
#include "pn532.h"
#include "rfid.hpp"
#include "configuration.hpp"

namespace {
    // constexpr gpio_num_t SCL_PIN = GPIO_NUM_10;
    // constexpr gpio_num_t SDA_PIN = GPIO_NUM_11;
    // constexpr gpio_num_t RESET_PIN = GPIO_NUM_NC;
    // constexpr gpio_num_t IRQ_PIN = GPIO_NUM_NC;
    static const char *TAG = "ntag_read";
    pn532_io_t pn532_io;
}

bool RFID::init()
{
	esp_err_t err;

	esp_log_level_set("PN532", ESP_LOG_DEBUG);
	esp_log_level_set("pn532_driver", ESP_LOG_DEBUG);
	esp_log_level_set("pn532_driver_i2c", ESP_LOG_DEBUG);
	esp_log_level_set("i2c.master", ESP_LOG_DEBUG);

    //pauses for 1 second to wait for peripherals to stabilize before starting PN532 
	vTaskDelay(1000 / portTICK_PERIOD_MS);

    //logs information to console 
	ESP_LOGI(TAG, "init PN532 in I2C mode");

    //halts execution if pin execution fails 
	ESP_ERROR_CHECK(pn532_new_driver_i2c(config::RFID_SDA_PIN, config::RFID_SCL_PIN, config::RFID_RESET_PIN, config::RFID_IRQ_PIN, 0, &pn532_io));

    //pn532 setup repeatedly until complete 
	do {

		err = pn532_init(&pn532_io); //pn532 startup commnd 
		
        if (err != ESP_OK) {
			ESP_LOGW(TAG, "failed to initialize PN532");
			pn532_release(&pn532_io);
			vTaskDelay(1000 / portTICK_PERIOD_MS); //delay and go again 
		}

	} while (err != ESP_OK);

	ESP_LOGI(TAG, "get firmware version");
	uint32_t version_data = 0;

	do {
	
        err = pn532_get_firmware_version(&pn532_io, &version_data);
	
        if (err != ESP_OK) {
			ESP_LOGI(TAG, "Didn't find PN53x board");
			pn532_reset(&pn532_io);
			vTaskDelay(1000 / portTICK_PERIOD_MS);
		}
	
    } while (err != ESP_OK);

	ESP_LOGI(TAG, "Found chip PN5%x", (unsigned int)(version_data >> 24) & 0xFF);
	ESP_LOGI(TAG, "Firmware ver. %d.%d",
			 (int)(version_data >> 16) & 0xFF,
			 (int)(version_data >> 8) & 0xFF);

	return true;
}

bool RFID::scan()
{
	ESP_LOGI(TAG, "Waiting for an ISO14443A Card ...");

	while (true) {
		uint8_t uid[] = {0, 0, 0, 0, 0, 0, 0};
		uint8_t uid_length;
		esp_err_t err = pn532_read_passive_target_id(
			&pn532_io,
			PN532_BRTY_ISO14443A_106KBPS,
			uid,
			&uid_length,
			0);

		if (err != ESP_OK) {
			continue;
		}

		ESP_LOGI(TAG, "\nFound an ISO14443A card");
		ESP_LOGI(TAG, "UID Length: %d bytes", uid_length);
		ESP_LOGI(TAG, "UID Value:");
		ESP_LOG_BUFFER_HEX_LEVEL(TAG, uid, uid_length, ESP_LOG_INFO);

		err = pn532_in_list_passive_target(&pn532_io);
		if (err != ESP_OK) {
			ESP_LOGI(TAG, "Failed to inList passive target");
			continue;
		}

		NTAG2XX_MODEL ntag_model = NTAG2XX_UNKNOWN;
		err = ntag2xx_get_model(&pn532_io, &ntag_model);
		if (err != ESP_OK) {
			continue;
		}

		int page_max;
		switch (ntag_model) {
			case NTAG2XX_NTAG213:
				page_max = 45;
				ESP_LOGI(TAG, "found NTAG213 target (or maybe NTAG203)");
				break;

			case NTAG2XX_NTAG215:
				page_max = 135;
				ESP_LOGI(TAG, "found NTAG215 target");
				break;

			case NTAG2XX_NTAG216:
				page_max = 231;
				ESP_LOGI(TAG, "found NTAG216 target");
				break;

			default:
				ESP_LOGI(TAG, "Found unknown NTAG target!");
				continue;
		}

		for (int page = 0; page < page_max; page += 4) {
			uint8_t buffer[16];
			err = ntag2xx_read_page(&pn532_io, page, buffer, 16);
			if (err == ESP_OK) {
				ESP_LOG_BUFFER_HEXDUMP(TAG, buffer, 16, ESP_LOG_INFO);
			} else {
				ESP_LOGI(TAG, "Failed to read page %d", page);
				break;
			}
		}

		vTaskDelay(1000 / portTICK_PERIOD_MS);
	}
}

// bool RFID::set()
// {
// 	printf("Setting RFID card information to.. ");

// 	int main(int argc, char const *argv[])
// 	{
// 		/* code */
// 		return 0;
// 	}
	

// 	//Update info to database 
// }