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
#include "esp_lcd_touch_xpt2046.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "setter_display.hpp"
#include "configuration.hpp"
#include "font8x8.hpp"


namespace {
    static const char *TAG = "setter_display";

    constexpr spi_host_device_t TFT_SPI_HOST = SPI2_HOST;
    constexpr int SPI_CLOCK_HZ = 40 * 1000 * 1000;   // was 20MHz; try this first, drop back down if the screen glitches/tears
    constexpr int BAND_LINES = 16;                   // pixel rows sent per SPI transfer
    constexpr int GLYPH_SIZE = 8;

    // The ST7796S is natively 320x480 portrait; SWAP_XY makes it 480x320 landscape.
    // Picture upside down: set both MIRROR flags to true. Colors inverted: flip INVERT_COLORS.
    constexpr bool SWAP_XY = true;
    constexpr bool MIRROR_X = false;
    constexpr bool MIRROR_Y = false;
    constexpr bool INVERT_COLORS = false;

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


bool SETTER_DISPLAY::init()
{
    ESP_LOGI(TAG, "init TFT display");

    // The touch controller shares the SPI bus, keep it deselected
    ESP_RETURN_ON_FALSE(output_high(config::TOUCH_CS_PIN), false, TAG, "failed to deselect touch controller");

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
    panel_cfg.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_BGR;
    panel_cfg.bits_per_pixel = 16;
    TRY(esp_lcd_new_panel_st7796(io_, &panel_cfg, &panel_));

    TRY(esp_lcd_panel_reset(panel_));
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

    if (!init_touch()) {
        ESP_LOGW(TAG, "touch controller not available, continuing without touch");
    }

    ESP_LOGI(TAG, "initialized display!");
    return true;
}

bool SETTER_DISPLAY::init_touch()
{
    // Built by hand rather than with ESP_LCD_TOUCH_IO_SPI_XPT2046_CONFIG: that macro
    // doesn't set every field, which this project's -Werror=missing-field-initializers rejects.
    esp_lcd_panel_io_spi_config_t tp_io_cfg = {};
    tp_io_cfg.cs_gpio_num = config::TOUCH_CS_PIN;
    tp_io_cfg.dc_gpio_num = GPIO_NUM_NC;
    tp_io_cfg.spi_mode = 0;
    tp_io_cfg.pclk_hz = ESP_LCD_TOUCH_SPI_CLOCK_HZ;
    tp_io_cfg.trans_queue_depth = 3;
    tp_io_cfg.lcd_cmd_bits = 8;
    tp_io_cfg.lcd_param_bits = 8;
    esp_lcd_panel_io_handle_t tp_io = nullptr;
    TRY(esp_lcd_new_panel_io_spi(static_cast<esp_lcd_spi_bus_handle_t>(TFT_SPI_HOST), &tp_io_cfg, &tp_io));

    esp_lcd_touch_config_t tp_cfg = {};
    // x_max/y_max are the panel's *native* (pre-swap) resolution: it's wired the same
    // portrait orientation as the LCD, so this is HEIGHT x WIDTH, not WIDTH x HEIGHT.
    tp_cfg.x_max = HEIGHT;
    tp_cfg.y_max = WIDTH;
    tp_cfg.rst_gpio_num = GPIO_NUM_NC;
    tp_cfg.int_gpio_num = GPIO_NUM_NC;   // polled, not interrupt-driven
    tp_cfg.flags.swap_xy = 1;            // matches SETTER_DISPLAY's SWAP_XY
    // If a drag moves the wrong way once this is on hardware, flip mirror_x / mirror_y below.
    tp_cfg.flags.mirror_x = 0;
    tp_cfg.flags.mirror_y = 0;
    TRY(esp_lcd_touch_new_spi_xpt2046(tp_io, &tp_cfg, &touch_));

    return true;
}


bool SETTER_DISPLAY::fill_screen(uint16_t color)
{
    std::fill_n(band_, WIDTH * BAND_LINES, swap_bytes(color));

    for (int y = 0; y < HEIGHT; y += BAND_LINES) {
        if (!blit(0, y, WIDTH, std::min(BAND_LINES, HEIGHT - y))) {
            return false;
        }
    }
    return true;
}

bool SETTER_DISPLAY::draw_text_centered(const char *text, int scale, uint16_t fg, uint16_t bg)
{
    const int len = static_cast<int>(strlen(text));
    if (len == 0) {
        return true;
    }

    const int w = len * GLYPH_SIZE * scale;
    const int h = GLYPH_SIZE * scale;
    ESP_RETURN_ON_FALSE(h <= HEIGHT, false, TAG, "text does not fit vertically");
    return draw_text_at(text, (WIDTH - w) / 2, (HEIGHT - h) / 2, scale, fg, bg);
}

bool SETTER_DISPLAY::draw_text_at(const char *text, int x, int y, int scale, uint16_t fg, uint16_t bg)
{
    const int len = static_cast<int>(strlen(text));
    if (len == 0) {
        return true;
    }

    const int w = len * GLYPH_SIZE * scale;
    ESP_RETURN_ON_FALSE(scale >= 1 && scale <= BAND_LINES && x >= 0 && w <= WIDTH - x,
                        false, TAG, "text does not fit horizontally");

    const uint16_t fg_px = swap_bytes(fg);
    const uint16_t bg_px = swap_bytes(bg);

    // one font row at a time: build a single pixel line, copy it `scale` times, send the strip.
    // every one of those `scale` copies is identical, so a row that's partly off the top or
    // bottom of the screen just needs fewer copies sent, not different ones.
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

bool SETTER_DISPLAY::poll_touch(int &x, int &y)
{
    if (!touch_ || esp_lcd_touch_read_data(touch_) != ESP_OK) {
        return false;
    }

    esp_lcd_touch_point_data_t point;
    uint8_t count = 0;
    if (esp_lcd_touch_get_data(touch_, &point, &count, 1) != ESP_OK || count == 0) {
        return false;
    }

    x = std::clamp<int>(point.x, 0, WIDTH - 1);
    y = std::clamp<int>(point.y, 0, HEIGHT - 1);
    return true;
}

bool SETTER_DISPLAY::run_scrollable_list(const char *const *items, int item_count, int scale)
{
    ESP_RETURN_ON_FALSE(item_count > 0, false, TAG, "item list is empty");

    const bool can_scroll = (touch_ != nullptr);
    if (!can_scroll) {
        ESP_LOGW(TAG, "touch not available, showing list without scrolling");
    }

    constexpr int MARGIN = 8;
    constexpr int ROW_PADDING = 12;   // extra vertical space per row, split above/below the text
    const int row_height = GLYPH_SIZE * scale + ROW_PADDING;
    const int content_height = item_count * row_height;
    const int max_scroll = std::max(0, content_height - HEIGHT);

    int scroll_y = 0;
    bool dragging = false;
    int drag_start_touch_y = 0;
    int drag_start_scroll_y = 0;
    int last_drawn_scroll_y = -1;   // forces the first draw

    while (true) {
        int touch_x = 0, touch_y = 0;
        const bool touched = can_scroll && poll_touch(touch_x, touch_y);

        if (touched && !dragging) {
            dragging = true;
            drag_start_touch_y = touch_y;
            drag_start_scroll_y = scroll_y;
        } else if (touched && dragging) {
            // finger moves up (touch_y decreases) -> content scrolls up, revealing what's below
            const int delta = drag_start_touch_y - touch_y;
            scroll_y = std::clamp(drag_start_scroll_y + delta, 0, max_scroll);
        } else {
            dragging = false;
        }

        // redrawing means a full-screen refill (see NOTES.md / conversation: no framebuffer,
        // no PSRAM, so there's no way to shift existing pixels), so skip it unless something moved
        if (scroll_y != last_drawn_scroll_y) {
            if (!fill_screen(WHITE)) {
                return false;
            }
            for (int i = 0; i < item_count; i++) {
                const int item_top = i * row_height - scroll_y;
                if (item_top + row_height <= 0 || item_top >= HEIGHT) {
                    continue;   // fully off-screen
                }
                if (!draw_text_at(items[i], MARGIN, item_top + ROW_PADDING / 2, scale, BLACK, WHITE)) {
                    return false;
                }
            }
            last_drawn_scroll_y = scroll_y;
        }

        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

bool SETTER_DISPLAY::blit(int x, int y, int w, int h)
{
    TRY(esp_lcd_panel_draw_bitmap(panel_, x, y, x + w, y + h, band_));
    ESP_RETURN_ON_FALSE(xSemaphoreTake(color_done_, pdMS_TO_TICKS(1000)) == pdTRUE,
                        false, TAG, "timed out waiting for SPI transfer");
    return true;
}
