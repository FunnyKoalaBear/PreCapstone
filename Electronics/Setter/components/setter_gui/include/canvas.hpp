#pragma once

#include <stdint.h>
#include <algorithm>

#include "setter_display.hpp"

// Drawing surface for the whole 480x320 screen without a full-screen buffer (no PSRAM for 300 KB).
// render() runs the draw function once per 16-row band and keeps only that band's pixels, then sends it.
// So a draw function must paint every pixel of its area each time (start with a background fill).
class CANVAS
{
public:
    static constexpr int WIDTH = SETTER_DISPLAY::WIDTH;
    static constexpr int HEIGHT = SETTER_DISPLAY::HEIGHT;
    static constexpr int BAND = SETTER_DISPLAY::MAX_PIXEL_ROWS;

    // where (x, y) sits on the text, like TFT_eSPI's datums
    enum datum_t { TOP_LEFT, MIDDLE_LEFT, MIDDLE_CENTER, MIDDLE_RIGHT };

    bool init();

    // Redraws screen rows y0..y1-1 by calling draw(*this) once per band
    template <typename F>
    bool render(SETTER_DISPLAY &display, int y0, int y1, F &&draw)
    {
        for (int y = std::max(y0, 0); y < std::min(y1, HEIGHT); y += BAND) {
            band_y_ = y;
            band_h_ = std::min(BAND, std::min(y1, HEIGHT) - y);
            draw(*this);
            if (!display.draw_pixels(0, band_y_, WIDTH, band_h_, buf_)) {
                return false;
            }
        }
        return true;
    }

    static constexpr uint16_t rgb(int r, int g, int b)
    {
        return static_cast<uint16_t>(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
    }

    void fill_screen(uint16_t c) { fill_rect(0, 0, WIDTH, HEIGHT, c); }
    void fill_rect(int x, int y, int w, int h, uint16_t c);
    void draw_rect(int x, int y, int w, int h, uint16_t c);
    void hline(int x, int y, int w, uint16_t c) { fill_rect(x, y, w, 1, c); }
    void vline(int x, int y, int h, uint16_t c) { fill_rect(x, y, 1, h, c); }
    void fill_round_rect(int x, int y, int w, int h, int r, uint16_t c);
    void draw_round_rect(int x, int y, int w, int h, int r, uint16_t c);
    void fill_circle(int cx, int cy, int r, uint16_t c);
    void fill_triangle(float x0, float y0, float x1, float y1, float x2, float y2, uint16_t c);
    void thick_line(float x0, float y0, float x1, float y1, int thickness, uint16_t c);

    // Fonts are the 8x8 font scaled like TFT_eSPI's numbers: 1 = 8x8, 2 = 8x16, 4 = 16x24.
    // UTF-8 (Japanese) characters are drawn as one '?' each.
    static int text_width(const char *s, int font);
    void draw_text(const char *s, int x, int y, int font, datum_t datum, uint16_t fg);

    // true if rows y..y+h-1 touch the band being drawn (lets callers skip work)
    bool in_band(int y, int h) const { return y < band_y_ + band_h_ && y + h > band_y_; }

private:
    void span(int y, int x0, int x1, uint16_t c);
    bool round_rect_span(int x, int y, int w, int h, int r, int row, int &x0, int &x1) const;

    uint16_t *buf_ = nullptr;
    int band_y_ = 0;
    int band_h_ = 0;
};
