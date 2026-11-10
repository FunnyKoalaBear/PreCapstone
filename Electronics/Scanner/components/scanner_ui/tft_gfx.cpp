#include <stdlib.h>
#include <string.h>
#include <utility>

#include "tft_gfx.hpp"


// TFT_eSPI's fonts, unchanged: font 1 = GLCD 5x7, font 2 = 16 px bitmap, font 4 = 26 px run-length encoded
#define PROGMEM
namespace tft_font_data {
#include "tft_fonts/glcdfont.c"
#include "tft_fonts/Font16.c"
#include "tft_fonts/Font32rle.c"
}
#undef PROGMEM


namespace {
    using namespace tft_font_data;

    constexpr int F2_HEIGHT = 16;   // chr_hgt_f16
    constexpr int F4_HEIGHT = 26;   // chr_hgt_f32

    // Reads one character from UTF-8 text: ASCII as is, any multi-byte character as '?'
    char next_char(const char *&p)
    {
        unsigned char c = static_cast<unsigned char>(*p++);
        if (c < 0x80) {
            return static_cast<char>(c);
        }
        while ((static_cast<unsigned char>(*p) & 0xC0) == 0x80) {
            p++;   // skip the continuation bytes
        }
        return '?';
    }

    int char_width(char ch, int font)
    {
        int code = static_cast<unsigned char>(ch);
        if (font == 1) {
            return 6;
        }
        if (code < 32 || code > 127) {
            code = 32;   // TFT_eSPI measures an unknown character as a space
        }
        return font == 4 ? widtbl_f32[code - 32] : widtbl_f16[code - 32];
    }
}


// ---------------------------------------------------------------- shapes (TFT_eSPI.cpp, same algorithms)

void TFT_GFX::draw_rect(int x, int y, int w, int h, uint16_t color)
{
    draw_fast_hline(x, y, w, color);
    draw_fast_hline(x, y + h - 1, w, color);
    draw_fast_vline(x, y + 1, h - 2, color);
    draw_fast_vline(x + w - 1, y + 1, h - 2, color);
}


void TFT_GFX::draw_line(int x0, int y0, int x1, int y1, uint16_t color)
{
    const bool steep = abs(y1 - y0) > abs(x1 - x0);
    if (steep) {
        std::swap(x0, y0);
        std::swap(x1, y1);
    }
    if (x0 > x1) {
        std::swap(x0, x1);
        std::swap(y0, y1);
    }
    const int dx = x1 - x0, dy = abs(y1 - y0);
    int err = dx >> 1, ystep = (y0 < y1) ? 1 : -1, xs = x0, dlen = 0;

    for (; x0 <= x1; x0++) {
        dlen++;
        err -= dy;
        if (err < 0) {
            if (steep) {
                draw_fast_vline(y0, xs, dlen, color);
            } else {
                draw_fast_hline(xs, y0, dlen, color);
            }
            dlen = 0;
            y0 += ystep;
            xs = x0 + 1;
            err += dx;
        }
    }
    if (dlen) {
        if (steep) {
            draw_fast_vline(y0, xs, dlen, color);
        } else {
            draw_fast_hline(xs, y0, dlen, color);
        }
    }
}


void TFT_GFX::draw_circle_helper(int x0, int y0, int rr, uint8_t corner, uint16_t color)
{
    if (rr <= 0) {
        return;
    }
    int f = 1 - rr, ddF_x = 1, ddF_y = -2 * rr, xe = 0, xs = 0, len = 0;

    do {
        while (f < 0) {
            ++xe;
            f += (ddF_x += 2);
        }
        f += (ddF_y += 2);

        if (xe - xs == 1) {
            if (corner & 0x1) { draw_pixel(x0 - xe, y0 - rr, color); draw_pixel(x0 - rr, y0 - xe, color); }
            if (corner & 0x2) { draw_pixel(x0 + rr, y0 - xe, color); draw_pixel(x0 + xs + 1, y0 - rr, color); }
            if (corner & 0x4) { draw_pixel(x0 + xs + 1, y0 + rr, color); draw_pixel(x0 + rr, y0 + xs + 1, color); }
            if (corner & 0x8) { draw_pixel(x0 - rr, y0 + xs + 1, color); draw_pixel(x0 - xe, y0 + rr, color); }
        } else {
            len = xe - xs++;
            if (corner & 0x1) { draw_fast_hline(x0 - xe, y0 - rr, len, color); draw_fast_vline(x0 - rr, y0 - xe, len, color); }
            if (corner & 0x2) { draw_fast_vline(x0 + rr, y0 - xe, len, color); draw_fast_hline(x0 + xs, y0 - rr, len, color); }
            if (corner & 0x4) { draw_fast_hline(x0 + xs, y0 + rr, len, color); draw_fast_vline(x0 + rr, y0 + xs, len, color); }
            if (corner & 0x8) { draw_fast_vline(x0 - rr, y0 + xs, len, color); draw_fast_hline(x0 - xe, y0 + rr, len, color); }
        }
        xs = xe;
    } while (xe < rr--);
}


void TFT_GFX::fill_circle_helper(int x0, int y0, int r, uint8_t corner, int delta, uint16_t color)
{
    int f = 1 - r, ddF_x = 1, ddF_y = -r - r, y = 0;
    delta++;

    while (y < r) {
        if (f >= 0) {
            if (corner & 0x1) draw_fast_hline(x0 - y, y0 + r, y + y + delta, color);
            if (corner & 0x2) draw_fast_hline(x0 - y, y0 - r, y + y + delta, color);
            r--;
            ddF_y += 2;
            f += ddF_y;
        }
        y++;
        ddF_x += 2;
        f += ddF_x;
        if (corner & 0x1) draw_fast_hline(x0 - r, y0 + y, r + r + delta, color);
        if (corner & 0x2) draw_fast_hline(x0 - r, y0 - y, r + r + delta, color);
    }
}


void TFT_GFX::draw_round_rect(int x, int y, int w, int h, int r, uint16_t color)
{
    draw_fast_hline(x + r, y, w - r - r, color);
    draw_fast_hline(x + r, y + h - 1, w - r - r, color);
    draw_fast_vline(x, y + r, h - r - r, color);
    draw_fast_vline(x + w - 1, y + r, h - r - r, color);
    draw_circle_helper(x + r, y + r, r, 1, color);
    draw_circle_helper(x + w - r - 1, y + r, r, 2, color);
    draw_circle_helper(x + w - r - 1, y + h - r - 1, r, 4, color);
    draw_circle_helper(x + r, y + h - r - 1, r, 8, color);
}


void TFT_GFX::fill_round_rect(int x, int y, int w, int h, int r, uint16_t color)
{
    if (!in_band(y, h)) {
        return;   // nothing of it in this band
    }
    fill_rect(x, y + r, w, h - r - r, color);
    fill_circle_helper(x + r, y + h - r - 1, r, 1, w - r - r - 1, color);
    fill_circle_helper(x + r, y + r, r, 2, w - r - r - 1, color);
}


void TFT_GFX::fill_circle(int x0, int y0, int r, uint16_t color)
{
    int x = 0, dx = 1, dy = r + r, p = -(r >> 1);

    draw_fast_hline(x0 - r, y0, dy + 1, color);
    while (x < r) {
        if (p >= 0) {
            draw_fast_hline(x0 - x, y0 + r, dx, color);
            draw_fast_hline(x0 - x, y0 - r, dx, color);
            dy -= 2;
            p -= dy;
            r--;
        }
        dx += 2;
        p += dx;
        x++;
        draw_fast_hline(x0 - r, y0 + x, dy + 1, color);
        draw_fast_hline(x0 - r, y0 - x, dy + 1, color);
    }
}


void TFT_GFX::fill_triangle(int x0, int y0, int x1, int y1, int x2, int y2, uint16_t color)
{
    if (y0 > y1) { std::swap(y0, y1); std::swap(x0, x1); }
    if (y1 > y2) { std::swap(y2, y1); std::swap(x2, x1); }
    if (y0 > y1) { std::swap(y0, y1); std::swap(x0, x1); }

    if (y0 == y2) {   // all on one line
        int a = x0, b = x0;
        if (x1 < a) a = x1; else if (x1 > b) b = x1;
        if (x2 < a) a = x2; else if (x2 > b) b = x2;
        draw_fast_hline(a, y0, b - a + 1, color);
        return;
    }

    const int dx01 = x1 - x0, dy01 = y1 - y0, dx02 = x2 - x0, dy02 = y2 - y0, dx12 = x2 - x1, dy12 = y2 - y1;
    int sa = 0, sb = 0, y;
    const int last = (y1 == y2) ? y1 : y1 - 1;

    for (y = y0; y <= last; y++) {
        int a = x0 + sa / dy01, b = x0 + sb / dy02;
        sa += dx01;
        sb += dx02;
        if (a > b) std::swap(a, b);
        draw_fast_hline(a, y, b - a + 1, color);
    }
    sa = dx12 * (y - y1);
    sb = dx02 * (y - y0);
    for (; y <= y2; y++) {
        int a = x1 + sa / dy12, b = x0 + sb / dy02;
        sa += dx12;
        sb += dx02;
        if (a > b) std::swap(a, b);
        draw_fast_hline(a, y, b - a + 1, color);
    }
}


// ---------------------------------------------------------------- text (TFT_eSPI drawString / drawChar)

int TFT_GFX::font_height(int font)
{
    return font == 4 ? F4_HEIGHT : font == 2 ? F2_HEIGHT : 8;
}


int TFT_GFX::text_width(const char *s, int font)
{
    int w = 0;
    for (const char *p = s; *p;) {
        w += char_width(next_char(p), font);
    }
    return w;
}


int TFT_GFX::draw_char(char ch, int x, int y, int font)
{
    const int code = static_cast<unsigned char>(ch);
    const int height = font_height(font);
    if (code < 32 || code > 127) {
        return 0;
    }
    const int width = char_width(ch, font);
    if (!in_band(y, height)) {
        return width;
    }
    if (bg_ != fg_) {
        fill_rect(x, y, width, height, bg_);   // opaque text paints its whole cell
    }

    if (font == 1) {
        // GLCD: 5 columns, bit 0 = top row
        for (int i = 0; i < 5; i++) {
            uint8_t line = tft_font_data::font[code * 5 + i];
            for (int j = 0; j < 8; j++, line >>= 1) {
                if (line & 0x1) {
                    draw_pixel(x + i, y + j, fg_);
                }
            }
        }
        return width;
    }

    if (font == 2) {
        // rows of (width + 6) / 8 bytes, most significant bit left
        const uint8_t *bits = chrtbl_f16[code - 32];
        const int row_bytes = (width + 6) / 8;
        for (int i = 0; i < F2_HEIGHT; i++) {
            if (!in_band(y + i, 1)) {
                continue;
            }
            int run = -1;   // start column of the current run of set pixels
            for (int col = 0; col <= row_bytes * 8; col++) {
                const bool on = col < row_bytes * 8 && ((bits[row_bytes * i + col / 8] >> (7 - col % 8)) & 1);
                if (on && run < 0) {
                    run = col;
                } else if (!on && run >= 0) {
                    draw_fast_hline(x + run, y + i, col - run, fg_);
                    run = -1;
                }
            }
        }
        return width;
    }

    // font 4, run-length encoded: bit 7 set = (n & 0x7F) + 1 lit pixels, else that many unlit, row after row
    const uint8_t *p = chrtbl_f32[code - 32];
    const int total = width * F4_HEIGHT;
    int pc = 0;
    while (pc < total) {
        const uint8_t b = *p++;
        int n = (b & 0x7F) + 1;
        if (!(b & 0x80)) {
            pc += n;
            continue;
        }
        while (n > 0 && pc < total) {   // a run may wrap onto the next row
            const int col = pc % width, row = pc / width;
            const int take = std::min(n, width - col);
            draw_fast_hline(x + col, y + row, take, fg_);
            pc += take;
            n -= take;
        }
    }
    return width;
}


int TFT_GFX::draw_string(const char *s, int x, int y, int font)
{
    const int w = text_width(s, font);
    const int h = font_height(font);

    switch (datum_) {
        case TR_DATUM: x -= w; break;
        case ML_DATUM: y -= h / 2; break;
        case MC_DATUM: x -= w / 2; y -= h / 2; break;
        case MR_DATUM: x -= w; y -= h / 2; break;
        default: break;
    }
    if (!in_band(y, h)) {
        return w;
    }
    for (const char *p = s; *p;) {
        x += draw_char(next_char(p), x, y, font);
    }
    return w;
}
