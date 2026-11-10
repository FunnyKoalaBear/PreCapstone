#include <math.h>
#include <string.h>
#include <stdint.h>

#include <esp_log.h>
#include "esp_heap_caps.h"

#include "canvas.hpp"


namespace {
    static const char *TAG = "canvas";

    // The panel wants the high byte first; the ESP32 stores the low byte first
    constexpr uint16_t swap_bytes(uint16_t v)
    {
        return static_cast<uint16_t>((v >> 8) | (v << 8));
    }

    // Reads one character from UTF-8 text and returns its code point ('?' for broken or 4-byte sequences)
    uint32_t next_char(const char *&p)
    {
        const unsigned char c = static_cast<unsigned char>(*p++);
        if (c < 0x80) {
            return c;
        }
        int extra = (c >= 0xE0 && c < 0xF0) ? 2 : (c >= 0xC0 && c < 0xE0) ? 1 : -1;
        uint32_t cp = (extra == 2) ? (c & 0x0F) : (c & 0x1F);
        for (int i = 0; i < extra; i++) {
            const unsigned char cc = static_cast<unsigned char>(*p);
            if ((cc & 0xC0) != 0x80) {
                return '?';
            }
            cp = (cp << 6) | (cc & 0x3F);
            p++;
        }
        while ((static_cast<unsigned char>(*p) & 0xC0) == 0x80) {
            p++;   // skip whatever is left of a sequence we can't use
        }
        return extra < 0 ? '?' : cp;
    }

    // Japanese bitmap fonts made by tools/make_jp_font.py, built into the app (EMBED_FILES in CMakeLists.txt)
    extern const uint8_t jp16_start[] asm("_binary_jp16_bin_start");
    extern const uint8_t jp24_start[] asm("_binary_jp24_bin_start");

    struct jp_font_t {
        const uint8_t *data;
        uint32_t count = 0;
        int size = 0;
        int row_bytes = 0;
        const uint8_t *codes = nullptr;     // count x uint16, sorted
        const uint8_t *bitmaps = nullptr;   // count x (size * row_bytes)

        explicit jp_font_t(const uint8_t *d) : data(d)
        {
            memcpy(&count, data, 4);
            uint32_t s = 0;
            memcpy(&s, data + 4, 4);
            size = static_cast<int>(s);
            row_bytes = (size + 7) / 8;
            codes = data + 8;
            bitmaps = codes + 2 * count;
        }

        // Binary search for the code point; nullptr if the font doesn't have it
        const uint8_t *find(uint32_t cp) const
        {
            uint32_t lo = 0, hi = count;
            while (lo < hi) {
                const uint32_t mid = (lo + hi) / 2;
                const uint16_t c = static_cast<uint16_t>(codes[2 * mid] | (codes[2 * mid + 1] << 8));
                if (c == cp) {
                    return bitmaps + mid * size * row_bytes;
                }
                if (c < cp) {
                    lo = mid + 1;
                } else {
                    hi = mid;
                }
            }
            return nullptr;
        }
    };

    const jp_font_t &jp_font(int font)
    {
        static const jp_font_t small(jp16_start);
        static const jp_font_t large(jp24_start);
        return (font == 4) ? large : small;
    }

    void font_scale(int font, int &sx, int &sy)
    {
        switch (font) {
            case 1:  sx = 1; sy = 1; break;
            case 4:  sx = 2; sy = 3; break;
            default: sx = 1; sy = 2; break;   // font 2
        }
    }
}


bool CANVAS::init()
{
    buf_ = static_cast<uint16_t *>(heap_caps_malloc(WIDTH * BAND * sizeof(uint16_t), MALLOC_CAP_DMA));
    if (!buf_) {
        ESP_LOGE(TAG, "no memory for the %d-row band buffer", BAND);
        return false;
    }
    return true;
}


// Core of every shape: one horizontal run of pixels [x0, x1) on row y, kept only if it is in this band
void CANVAS::span(int y, int x0, int x1, uint16_t c)
{
    if (y < band_y_ || y >= band_y_ + band_h_) {
        return;
    }
    x0 = std::max(x0, 0);
    x1 = std::min(x1, WIDTH);
    if (x0 >= x1) {
        return;
    }
    std::fill_n(buf_ + (y - band_y_) * WIDTH + x0, x1 - x0, swap_bytes(c));
}


void CANVAS::fill_rect(int x, int y, int w, int h, uint16_t c)
{
    const int top = std::max(y, band_y_);
    const int bottom = std::min(y + h, band_y_ + band_h_);
    for (int row = top; row < bottom; row++) {
        span(row, x, x + w, c);
    }
}


void CANVAS::draw_rect(int x, int y, int w, int h, uint16_t c)
{
    hline(x, y, w, c);
    hline(x, y + h - 1, w, c);
    vline(x, y, h, c);
    vline(x + w - 1, y, h, c);
}


// Left/right edge of a rounded rectangle on one row (false if the row is outside it)
bool CANVAS::round_rect_span(int x, int y, int w, int h, int r, int row, int &x0, int &x1) const
{
    if (row < y || row >= y + h || w <= 0 || h <= 0) {
        return false;
    }
    r = std::max(0, std::min(r, std::min(w, h) / 2));

    float dy = 0;
    if (row < y + r) {
        dy = (y + r) - row - 0.5f;
    } else if (row >= y + h - r) {
        dy = row - (y + h - r) + 0.5f;
    }
    const int inset = (dy > 0) ? static_cast<int>(roundf(r - sqrtf(std::max(0.0f, r * r - dy * dy)))) : 0;
    x0 = x + inset;
    x1 = x + w - inset;
    return true;
}


void CANVAS::fill_round_rect(int x, int y, int w, int h, int r, uint16_t c)
{
    const int top = std::max(y, band_y_);
    const int bottom = std::min(y + h, band_y_ + band_h_);
    for (int row = top; row < bottom; row++) {
        int x0, x1;
        if (round_rect_span(x, y, w, h, r, row, x0, x1)) {
            span(row, x0, x1, c);
        }
    }
}


// 1-pixel outline: the outer shape minus the shape one pixel smaller
void CANVAS::draw_round_rect(int x, int y, int w, int h, int r, uint16_t c)
{
    const int top = std::max(y, band_y_);
    const int bottom = std::min(y + h, band_y_ + band_h_);
    for (int row = top; row < bottom; row++) {
        int ox0, ox1, ix0, ix1;
        if (!round_rect_span(x, y, w, h, r, row, ox0, ox1)) {
            continue;
        }
        if (!round_rect_span(x + 1, y + 1, w - 2, h - 2, r - 1, row, ix0, ix1)) {
            span(row, ox0, ox1, c);   // top or bottom edge
            continue;
        }
        span(row, ox0, std::max(ix0, ox0 + 1), c);
        span(row, std::min(ix1, ox1 - 1), ox1, c);
    }
}


void CANVAS::fill_circle(int cx, int cy, int r, uint16_t c)
{
    const int top = std::max(cy - r, band_y_);
    const int bottom = std::min(cy + r + 1, band_y_ + band_h_);
    for (int row = top; row < bottom; row++) {
        const float dy = row - cy;
        const int dx = static_cast<int>(roundf(sqrtf(std::max(0.0f, r * r - dy * dy))));
        span(row, cx - dx, cx + dx + 1, c);
    }
}


// Scanline fill: for each row, fill between where the row's centre crosses the triangle's edges
void CANVAS::fill_triangle(float x0, float y0, float x1, float y1, float x2, float y2, uint16_t c)
{
    const float xs[3] = {x0, x1, x2};
    const float ys[3] = {y0, y1, y2};
    const int top = std::max(static_cast<int>(floorf(std::min({y0, y1, y2}))), band_y_);
    const int bottom = std::min(static_cast<int>(ceilf(std::max({y0, y1, y2}))) + 1, band_y_ + band_h_);

    for (int row = top; row < bottom; row++) {
        const float yc = row + 0.5f;
        float lo = 1e9f, hi = -1e9f;
        for (int e = 0; e < 3; e++) {
            const float ax = xs[e], ay = ys[e], bx = xs[(e + 1) % 3], by = ys[(e + 1) % 3];
            if (ay == by || yc < std::min(ay, by) || yc > std::max(ay, by)) {
                continue;
            }
            const float x = ax + (yc - ay) * (bx - ax) / (by - ay);
            lo = std::min(lo, x);
            hi = std::max(hi, x);
        }
        if (lo <= hi) {
            const int a = static_cast<int>(ceilf(lo - 0.5f));
            const int b = static_cast<int>(floorf(hi - 0.5f)) + 1;
            span(row, a, std::max(b, a + 1), c);
        }
    }
}


// A line `thickness` pixels wide, drawn as two triangles (same as the SetterUI sketch)
void CANVAS::thick_line(float x0, float y0, float x1, float y1, int thickness, uint16_t c)
{
    const float dx = x1 - x0, dy = y1 - y0;
    const float len = sqrtf(dx * dx + dy * dy);
    if (len < 0.1f) {
        return;
    }
    const float nx = -dy / len * (thickness * 0.5f);
    const float ny = dx / len * (thickness * 0.5f);
    fill_triangle(x0 + nx, y0 + ny, x0 - nx, y0 - ny, x1 - nx, y1 - ny, c);
    fill_triangle(x0 + nx, y0 + ny, x1 - nx, y1 - ny, x1 + nx, y1 + ny, c);
}


int CANVAS::text_width(const char *s, int font)
{
    int sx, sy;
    font_scale(font, sx, sy);
    const jp_font_t &jp = jp_font(font);
    int w = 0;
    for (const char *p = s; *p;) {
        const uint32_t cp = next_char(p);
        w += (cp >= 0x80 && jp.find(cp)) ? jp.size : 8 * sx;
    }
    return w;
}


void CANVAS::draw_text(const char *s, int x, int y, int font, datum_t datum, uint16_t fg)
{
    int sx, sy;
    font_scale(font, sx, sy);
    const jp_font_t &jp = jp_font(font);
    const int h = 8 * sy;   // the line height used for the datum; Japanese is centred on the same line

    if (datum != TOP_LEFT && datum != TOP_RIGHT) {
        y -= h / 2;
    }
    if (datum == MIDDLE_CENTER) {
        x -= text_width(s, font) / 2;
    } else if (datum == MIDDLE_RIGHT || datum == TOP_RIGHT) {
        x -= text_width(s, font);
    }
    const int jp_y = y + (h - jp.size) / 2;
    if (!in_band(std::min(y, jp_y), std::max(h, jp.size))) {
        return;
    }

    for (const char *p = s; *p;) {
        const uint32_t cp = next_char(p);
        const uint8_t *big = (cp >= 0x80) ? jp.find(cp) : nullptr;

        if (big) {
            // Japanese glyph: one bit per pixel, most significant bit on the left
            for (int row = 0; row < jp.size; row++) {
                const int py = jp_y + row;
                if (!in_band(py, 1)) {
                    continue;
                }
                const uint8_t *bits = big + row * jp.row_bytes;
                for (int col = 0; col < jp.size;) {
                    if (!(bits[col / 8] & (0x80 >> (col % 8)))) {
                        col++;
                        continue;
                    }
                    int end = col;
                    while (end < jp.size && (bits[end / 8] & (0x80 >> (end % 8)))) {
                        end++;
                    }
                    span(py, x + col, x + end, fg);
                    col = end;
                }
            }
            x += jp.size;
            continue;
        }

        const uint8_t *g = SCANNER_DISPLAY::glyph(cp < 0x80 ? static_cast<char>(cp) : '?');
        for (int row = 0; row < 8; row++) {
            const int py = y + row * sy;
            if (!in_band(py, sy)) {
                continue;
            }
            // one span per run of set bits, `sy` rows tall
            for (int col = 0; col < 8;) {
                if (!((g[row] >> col) & 1)) {
                    col++;
                    continue;
                }
                int end = col;
                while (end < 8 && ((g[row] >> end) & 1)) {
                    end++;
                }
                for (int k = 0; k < sy; k++) {
                    span(py + k, x + col * sx, x + end * sx, fg);
                }
                col = end;
            }
        }
        x += 8 * sx;
    }
}
