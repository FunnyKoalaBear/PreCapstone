#include <algorithm>
#include <string.h>

#include <esp_log.h>
#include "esp_attr.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_lcd_io_spi.h"
#include "esp_lcd_st7796.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "scanner_display.hpp"
#include "configuration.hpp"
#include "font8x8.hpp"


namespace {
    static const char *TAG = "scanner_display";

    constexpr spi_host_device_t TFT_SPI_HOST = SPI2_HOST;
    constexpr int SPI_CLOCK_HZ = 20 * 1000 * 1000;   // start at 20MHz on the new panel; raise to 40MHz once it works
    constexpr int BAND_LINES = SCANNER_DISPLAY::MAX_PIXEL_ROWS;   // pixel rows sent per SPI transfer
    constexpr int GLYPH_SIZE = 8;

    // The ST7796S is natively 320x480 portrait; SWAP_XY makes it 480x320 landscape.
    // Picture upside down: flip both MIRROR flags. White shows as black: flip INVERT_COLORS.
    // Red and blue swapped: change RGB_ORDER to LCD_RGB_ELEMENT_ORDER_RGB.
    constexpr bool SWAP_XY = true;
    constexpr bool MIRROR_X = false;
    constexpr bool MIRROR_Y = false;
    constexpr bool INVERT_COLORS = true;   // IPS panels usually need inversion on
    constexpr lcd_rgb_element_order_t RGB_ORDER = LCD_RGB_ELEMENT_ORDER_BGR;

    #define TRY(call)                                                                                \
        do {                                                                                         \
            esp_err_t err_ = (call);                                                                 \
            ESP_RETURN_ON_FALSE(err_ == ESP_OK, false, TAG, #call " failed: %s", esp_err_to_name(err_)); \
        } while (0)

    // The panel expects each RGB565 pixel high byte first, the ESP32 stores it low byte first.
    constexpr uint16_t swap_bytes(uint16_t v)
    {
        return static_cast<uint16_t>((v >> 8) | (v << 8));
    }

    const uint8_t *glyph_for(char c)
    {
        if (c == FONT_HEART_CHAR) {
            return FONT_HEART;
        }

        int code = static_cast<unsigned char>(c);
        if (code < FONT_FIRST_CHAR || code > FONT_LAST_CHAR) {
            code = '?';
        }
        return FONT8X8[code - FONT_FIRST_CHAR];
    }

    int text_width(const char *text, int scale)
    {
        return static_cast<int>(strlen(text)) * GLYPH_SIZE * scale;
    }

    // largest scale up to `scale` that fits the text on one screen line
    int fit_scale(const char *text, int scale)
    {
        while (scale > 1 && text_width(text, scale) > SCANNER_DISPLAY::WIDTH) {
            scale--;
        }
        return scale;
    }

    bool output_high(gpio_num_t pin)
    {
        if (pin == GPIO_NUM_NC) {
            return true;
        }

        gpio_config_t cfg = {};
        cfg.pin_bit_mask = 1ULL << pin;
        cfg.mode = GPIO_MODE_OUTPUT;
        if (gpio_config(&cfg) != ESP_OK) {
            return false;
        }
        return gpio_set_level(pin, 1) == ESP_OK;
    }

    // Runs in ISR context when a color transfer finishes.
    bool IRAM_ATTR on_color_trans_done(esp_lcd_panel_io_handle_t, esp_lcd_panel_io_event_data_t *, void *user_ctx)
    {
        BaseType_t woken = pdFALSE;
        xSemaphoreGiveFromISR(static_cast<SemaphoreHandle_t>(user_ctx), &woken);
        return woken == pdTRUE;
    }
}


bool SCANNER_DISPLAY::init()
{
    ESP_LOGI(TAG, "init scanner display");

    color_done_ = xSemaphoreCreateBinary();
    band_ = static_cast<uint16_t *>(heap_caps_malloc(WIDTH * BAND_LINES * sizeof(uint16_t), MALLOC_CAP_DMA));
    ESP_RETURN_ON_FALSE(color_done_ && band_, false, TAG, "out of memory");

    spi_bus_config_t bus_cfg = {};
    bus_cfg.mosi_io_num = config::TFT_MOSI_PIN;
    bus_cfg.miso_io_num = config::TFT_MISO_PIN;
    bus_cfg.sclk_io_num = config::TFT_SCK_PIN;
    bus_cfg.quadwp_io_num = -1;
    bus_cfg.quadhd_io_num = -1;
    bus_cfg.max_transfer_sz = WIDTH * BAND_LINES * sizeof(uint16_t);
    TRY(spi_bus_initialize(TFT_SPI_HOST, &bus_cfg, SPI_DMA_CH_AUTO));

    esp_lcd_panel_io_spi_config_t io_cfg = {};
    io_cfg.cs_gpio_num = config::TFT_CS_PIN;
    io_cfg.dc_gpio_num = config::TFT_DC_PIN;
    io_cfg.spi_mode = 0;
    io_cfg.pclk_hz = SPI_CLOCK_HZ;
    io_cfg.trans_queue_depth = 10;
    io_cfg.lcd_cmd_bits = 8;
    io_cfg.lcd_param_bits = 8;
    io_cfg.on_color_trans_done = on_color_trans_done;
    io_cfg.user_ctx = color_done_;
    TRY(esp_lcd_new_panel_io_spi(static_cast<esp_lcd_spi_bus_handle_t>(TFT_SPI_HOST), &io_cfg, &io_));

    esp_lcd_panel_dev_config_t panel_cfg = {};
    panel_cfg.reset_gpio_num = config::TFT_RESET_PIN;
    panel_cfg.rgb_ele_order = RGB_ORDER;
    panel_cfg.bits_per_pixel = 16;
    TRY(esp_lcd_new_panel_st7796(io_, &panel_cfg, &panel_));

    TRY(esp_lcd_panel_reset(panel_));
    vTaskDelay(pdMS_TO_TICKS(120));   // give the panel time to wake after reset
    TRY(esp_lcd_panel_init(panel_));
    TRY(esp_lcd_panel_invert_color(panel_, INVERT_COLORS));
    TRY(esp_lcd_panel_swap_xy(panel_, SWAP_XY));
    TRY(esp_lcd_panel_mirror(panel_, MIRROR_X, MIRROR_Y));

    // paint white before switching the display on so power-on noise is never visible
    if (!fill_screen(WHITE)) {
        return false;
    }
    TRY(esp_lcd_panel_disp_on_off(panel_, true));
    output_high(config::TFT_BACKLIGHT_PIN);

    ESP_LOGI(TAG, "initialized scanner display!");
    return true;
}


bool SCANNER_DISPLAY::fill_screen(uint16_t color)
{
    return fill_rect(0, 0, WIDTH, HEIGHT, color);
}

bool SCANNER_DISPLAY::fill_rect(int x, int y, int w, int h, uint16_t color)
{
    // clip to the screen so callers can pass rectangles that hang off the edge
    const int x0 = std::max(x, 0);
    const int y0 = std::max(y, 0);
    const int x1 = std::min(x + w, WIDTH);
    const int y1 = std::min(y + h, HEIGHT);
    if (x0 >= x1 || y0 >= y1) {
        return true;
    }

    const int clipped_w = x1 - x0;
    std::fill_n(band_, clipped_w * BAND_LINES, swap_bytes(color));

    for (int row = y0; row < y1; row += BAND_LINES) {
        if (!blit(x0, row, clipped_w, std::min(BAND_LINES, y1 - row))) {
            return false;
        }
    }
    return true;
}

bool SCANNER_DISPLAY::draw_text_centered(const char *text, int scale, uint16_t fg, uint16_t bg)
{
    const int len = static_cast<int>(strlen(text));
    if (len == 0) {
        return true;
    }

    const int w = text_width(text, scale);
    const int h = GLYPH_SIZE * scale;
    ESP_RETURN_ON_FALSE(h <= HEIGHT, false, TAG, "text does not fit vertically");
    return draw_text_at(text, (WIDTH - w) / 2, (HEIGHT - h) / 2, scale, fg, bg);
}

bool SCANNER_DISPLAY::draw_text_at(const char *text, int x, int y, int scale, uint16_t fg, uint16_t bg)
{
    const int len = static_cast<int>(strlen(text));
    if (len == 0) {
        return true;
    }

    const int w = text_width(text, scale);
    ESP_RETURN_ON_FALSE(scale >= 1 && scale <= BAND_LINES && x >= 0 && w <= WIDTH - x,
                        false, TAG, "text does not fit horizontally");

    const uint16_t fg_px = swap_bytes(fg);
    const uint16_t bg_px = swap_bytes(bg);

    // one font row at a time: build one pixel line, copy it `scale` times, send the strip
    for (int row = 0; row < GLYPH_SIZE; row++) {
        const int row_top = y + row * scale;
        const int visible_top = std::max(row_top, 0);
        const int visible_h = std::min(row_top + scale, HEIGHT) - visible_top;
        if (visible_h <= 0) {
            continue;
        }

        uint16_t *out = band_;
        for (int c = 0; c < len; c++) {
            const uint8_t bits = glyph_for(text[c])[row];
            for (int col = 0; col < GLYPH_SIZE; col++) {
                const uint16_t px = ((bits >> col) & 1) ? fg_px : bg_px;
                std::fill_n(out, scale, px);
                out += scale;
            }
        }
        for (int i = 1; i < visible_h; i++) {
            memcpy(band_ + i * w, band_, w * sizeof(uint16_t));
        }

        if (!blit(x, visible_top, w, visible_h)) {
            return false;
        }
    }
    return true;
}

bool SCANNER_DISPLAY::show_status(const char *title, const char *detail, uint16_t fg, uint16_t bg)
{
    constexpr int TITLE_SCALE = 5;
    constexpr int DETAIL_SCALE = 3;
    constexpr int GAP = 24;   // pixels between the two lines

    if (!fill_screen(bg)) {
        return false;
    }

    const int title_scale = fit_scale(title, TITLE_SCALE);
    const int detail_scale = fit_scale(detail, DETAIL_SCALE);
    const int title_h = GLYPH_SIZE * title_scale;
    const int top = (HEIGHT - (title_h + GAP + GLYPH_SIZE * detail_scale)) / 2;

    return draw_text_at(title, (WIDTH - text_width(title, title_scale)) / 2, top, title_scale, fg, bg)
        && draw_text_at(detail, (WIDTH - text_width(detail, detail_scale)) / 2, top + title_h + GAP, detail_scale, fg, bg);
}

bool SCANNER_DISPLAY::draw_pixels(int x, int y, int w, int h, const uint16_t *pixels)
{
    ESP_RETURN_ON_FALSE(w * h <= WIDTH * MAX_PIXEL_ROWS, false, TAG, "pixel block too big");
    TRY(esp_lcd_panel_draw_bitmap(panel_, x, y, x + w, y + h, pixels));
    ESP_RETURN_ON_FALSE(xSemaphoreTake(color_done_, pdMS_TO_TICKS(1000)) == pdTRUE,
                        false, TAG, "timed out waiting for SPI transfer");
    return true;
}

const uint8_t *SCANNER_DISPLAY::glyph(char c)
{
    return glyph_for(c);
}

bool SCANNER_DISPLAY::blit(int x, int y, int w, int h)
{
    TRY(esp_lcd_panel_draw_bitmap(panel_, x, y, x + w, y + h, band_));
    ESP_RETURN_ON_FALSE(xSemaphoreTake(color_done_, pdMS_TO_TICKS(1000)) == pdTRUE,
                        false, TAG, "timed out waiting for SPI transfer");
    return true;
}
