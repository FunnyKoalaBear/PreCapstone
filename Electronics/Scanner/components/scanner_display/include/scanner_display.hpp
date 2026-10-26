#pragma once

#include <stdint.h>

#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

// 3.5" ST7796S IPS panel, display only (capacitive touch pins left unwired)
class SCANNER_DISPLAY
{

public:
    static constexpr int WIDTH  = 480;
    static constexpr int HEIGHT = 320;

    // RGB565 colors
    static constexpr uint16_t WHITE = 0xFFFF;
    static constexpr uint16_t BLACK = 0x0000;
    static constexpr uint16_t RED   = 0xF800;
    static constexpr uint16_t GREEN = 0x07E0;
    static constexpr uint16_t BLUE  = 0x001F;

    bool init();
    bool fill_screen(uint16_t color);
    bool fill_rect(int x, int y, int w, int h, uint16_t color);

    // scale multiplies the 8x8 font size, so scale 4 draws 32 pixel tall letters
    bool draw_text_centered(const char *text, int scale, uint16_t fg, uint16_t bg);

    // like draw_text_centered, but at an explicit position; y may be partly or fully off-screen
    bool draw_text_at(const char *text, int x, int y, int scale, uint16_t fg, uint16_t bg);

    // clears the screen to bg and draws a big title with a smaller detail line under it
    bool show_status(const char *title, const char *detail, uint16_t fg, uint16_t bg);


private:
    bool blit(int x, int y, int w, int h);

    esp_lcd_panel_io_handle_t io_ = nullptr;
    esp_lcd_panel_handle_t panel_ = nullptr;
    SemaphoreHandle_t color_done_ = nullptr;
    uint16_t *band_ = nullptr;
};
