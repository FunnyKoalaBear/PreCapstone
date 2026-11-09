#pragma once

#include <stdint.h>

#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_touch.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

class SETTER_DISPLAY
{
    
public:
    static constexpr int WIDTH  = 480;
    static constexpr int HEIGHT = 320;

    // RGB565 colors
    static constexpr uint16_t WHITE = 0xFFFF;
    static constexpr uint16_t BLACK = 0x0000;

    bool init();
    bool fill_screen(uint16_t color);
    
    // scale multiplies the 8x8 font size, so scale 4 draws 32 pixel tall letters
    bool draw_text_centered(const char *text, int scale, uint16_t fg, uint16_t bg);

    // like draw_text_centered, but at an explicit position; y may be partly or fully off-screen
    bool draw_text_at(const char *text, int x, int y, int scale, uint16_t fg, uint16_t bg);

    // true if a finger is on the screen right now; x/y are in screen pixels (0..WIDTH-1, 0..HEIGHT-1)
    bool poll_touch(int &x, int &y);

    // sends a block of pixels: RGB565 already byte-swapped, DMA-capable memory, at most MAX_PIXEL_ROWS full rows
    static constexpr int MAX_PIXEL_ROWS = 16;
    bool draw_pixels(int x, int y, int w, int h, const uint16_t *pixels);

    // 8x8 bitmap of an ASCII character (one byte per row, bit 0 = leftmost pixel); unknown characters give '?'
    static const uint8_t *glyph(char c);

    // draws `items` as a vertical list and lets a finger drag scroll it. Runs forever
    // (or until an item list is empty), so this owns the screen for the rest of the program.
    bool run_scrollable_list(const char *const *items, int item_count, int scale = 2);


private:
    bool init_touch();
    bool blit(int x, int y, int w, int h);

    esp_lcd_panel_io_handle_t io_ = nullptr;
    esp_lcd_panel_handle_t panel_ = nullptr;
    esp_lcd_touch_handle_t touch_ = nullptr;
    SemaphoreHandle_t color_done_ = nullptr;
    uint16_t *band_ = nullptr;
};
