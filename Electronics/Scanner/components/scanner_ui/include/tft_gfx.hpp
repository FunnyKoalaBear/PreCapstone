#pragma once

#include <stdint.h>

#include "canvas.hpp"

// TFT_eSPI-style drawing on one CANVAS band, so the ScannerUI sketch's code ports line by line.
// Shapes use TFT_eSPI's own algorithms and fonts 1, 2 and 4 are TFT_eSPI's, so the pixels match the sketch.
class TFT_GFX
{
public:
    enum datum_t { TL_DATUM, TR_DATUM, ML_DATUM, MC_DATUM, MR_DATUM };

    explicit TFT_GFX(CANVAS &canvas) : c_(canvas) {}

    static constexpr uint16_t color565(int r, int g, int b) { return CANVAS::rgb(r, g, b); }

    void fill_screen(uint16_t color) { fill_rect(0, 0, CANVAS::WIDTH, CANVAS::HEIGHT, color); }
    void fill_rect(int x, int y, int w, int h, uint16_t color) { c_.fill_rect(x, y, w, h, color); }
    void draw_pixel(int x, int y, uint16_t color) { c_.fill_rect(x, y, 1, 1, color); }
    void draw_fast_hline(int x, int y, int w, uint16_t color) { c_.fill_rect(x, y, w, 1, color); }
    void draw_fast_vline(int x, int y, int h, uint16_t color) { c_.fill_rect(x, y, 1, h, color); }
    void draw_rect(int x, int y, int w, int h, uint16_t color);
    void draw_line(int x0, int y0, int x1, int y1, uint16_t color);
    void fill_round_rect(int x, int y, int w, int h, int r, uint16_t color);
    void draw_round_rect(int x, int y, int w, int h, int r, uint16_t color);
    void fill_circle(int x0, int y0, int r, uint16_t color);
    void fill_triangle(int x0, int y0, int x1, int y1, int x2, int y2, uint16_t color);

    // setTextColor(fg) draws transparent text; setTextColor(fg, bg) also fills each character cell
    void set_text_color(uint16_t fg) { fg_ = bg_ = fg; }
    void set_text_color(uint16_t fg, uint16_t bg) { fg_ = fg; bg_ = bg; }
    void set_text_datum(datum_t datum) { datum_ = datum; }
    uint16_t text_color() const { return fg_; }
    uint16_t text_bg_color() const { return bg_; }
    datum_t text_datum() const { return datum_; }

    // drawString at the datum; returns the width. UTF-8 (Japanese) characters show as '?'.
    int draw_string(const char *s, int x, int y, int font);

    // drawChar with its top-left at (x, y); returns the advance in pixels
    int draw_char(char ch, int x, int y, int font);

    static int text_width(const char *s, int font);
    static int font_height(int font);

    bool in_band(int y, int h) const { return c_.in_band(y, h); }

private:
    void draw_circle_helper(int x0, int y0, int rr, uint8_t corner, uint16_t color);
    void fill_circle_helper(int x0, int y0, int r, uint8_t corner, int delta, uint16_t color);

    CANVAS &c_;
    uint16_t fg_ = 0xFFFF;
    uint16_t bg_ = 0xFFFF;
    datum_t datum_ = TL_DATUM;
};
