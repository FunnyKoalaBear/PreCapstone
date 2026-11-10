#include <stdio.h>
#include <string.h>
#include <time.h>

#include "scanner_gui.hpp"


namespace {
    using D = CANVAS;

    // colours from scanner_ui.ino
    constexpr uint16_t C_BG        = CANVAS::rgb(77, 77, 77);      // workspace grey
    constexpr uint16_t C_HEADER    = CANVAS::rgb(229, 171, 58);    // mustard header
    constexpr uint16_t C_PANEL_BG  = CANVAS::rgb(247, 247, 247);   // right pane
    constexpr uint16_t C_WHITE     = 0xFFFF;                       // blueprint pane
    constexpr uint16_t C_BLACK     = 0x0000;
    constexpr uint16_t C_CORAL     = CANVAS::rgb(240, 84, 79);     // order id, dates
    constexpr uint16_t C_TEXT      = CANVAS::rgb(9, 9, 9);
    constexpr uint16_t C_GREEN     = CANVAS::rgb(5, 150, 105);     // steps done
    constexpr uint16_t C_GREY      = CANVAS::rgb(212, 212, 216);   // steps to come, dividers
    constexpr uint16_t C_STRIP     = CANVAS::rgb(245, 245, 245);
    constexpr uint16_t C_LABEL     = CANVAS::rgb(115, 115, 115);
    constexpr uint16_t C_DIM       = CANVAS::rgb(64, 64, 64);
    constexpr uint16_t C_STRIP_TXT = CANVAS::rgb(38, 38, 38);
    constexpr uint16_t C_OUTLINE   = CANVAS::rgb(160, 160, 160);
    constexpr uint16_t C_MUSTARD_L = CANVAS::rgb(247, 235, 206);   // light mustard box
    constexpr uint16_t C_DARK      = CANVAS::rgb(20, 20, 20);

    constexpr int HEADER_H = 48;

    // Moves p past one UTF-8 character
    const char *next_utf8(const char *p)
    {
        if (*p) {
            p++;
        }
        while ((static_cast<unsigned char>(*p) & 0xC0) == 0x80) {
            p++;
        }
        return p;
    }

    // Copies text, cut to max_w pixels with ".." if it is too wide (never splits a UTF-8 character)
    void fit(const char *s, int font, int max_w, char *out, size_t size)
    {
        strlcpy(out, s, size);
        if (CANVAS::text_width(out, font) <= max_w) {
            return;
        }
        const int dots = CANVAS::text_width("..", font);
        size_t keep = 0;
        for (const char *p = s; *p;) {
            const char *q = next_utf8(p);
            const size_t len = q - s;
            if (len + 3 > size) {
                break;
            }
            memcpy(out, s, len);
            out[len] = '\0';
            if (CANVAS::text_width(out, font) + dots > max_w) {
                break;
            }
            keep = len;
            p = q;
        }
        out[keep] = '\0';
        strlcat(out, "..", size);
    }

    // draw_text that never runs past max_w
    void text_fit(CANVAS &c, const char *s, int x, int y, int font, CANVAS::datum_t datum, uint16_t fg, int max_w)
    {
        char buf[128];
        fit(s, font, max_w, buf, sizeof(buf));
        c.draw_text(buf, x, y, font, datum, fg);
    }

    // ---------------------------------------------------------------- "Poppins" big characters (from the sketch)

    // One "Poppins" character (digits, # : -); g == nullptr only measures. Returns the advance in pixels.
    int poppins(CANVAS *g, char ch, int x, int y, int h, int t, uint16_t fg, uint16_t bg)
    {
        const int w = (h * 72) / 100;
        switch (ch) {
            case ' ':
                return h / 3;
            case ':':
                if (g) {
                    g->fill_circle(x + t / 2 + 1, y + h / 3, t / 2 + 1, fg);
                    g->fill_circle(x + t / 2 + 1, y + (h * 3) / 4, t / 2 + 1, fg);
                }
                return t + 5;
            case '#': {
                const int wh = (h * 80) / 100;
                if (g) {
                    g->thick_line(x + wh * 0.35f, y + 1, x + wh * 0.20f, y + h - 1, t - 1, fg);
                    g->thick_line(x + wh * 0.75f, y + 1, x + wh * 0.60f, y + h - 1, t - 1, fg);
                    g->fill_rect(x + 1, y + h / 3 - t / 2, wh - 2, t - 1, fg);
                    g->fill_rect(x + 1, y + (h * 2) / 3 - t / 2, wh - 2, t - 1, fg);
                }
                return wh + 3;
            }
            case '-':
                if (g) {
                    g->fill_rect(x + 2, y + (h - t) / 2, w / 2, t, fg);
                }
                return w / 2 + 5;
            case '0':
                if (g) {
                    g->fill_round_rect(x, y, w, h, w / 2, fg);
                    g->fill_round_rect(x + t, y + t, w - 2 * t, h - 2 * t, (w - 2 * t) / 2, bg);
                }
                return w + 3;
            case '1':
                if (g) {
                    g->fill_rect(x + w / 2, y, t, h, fg);
                    g->fill_rect(x + w / 6, y, w / 2 - w / 6, t, fg);   // short flag (the sketch's full bar reads as 7)
                }
                return w / 2 + t + 4;
            case '2': {
                if (g) {
                    int bowl = (h * 52) / 100;
                    g->fill_round_rect(x, y, w, bowl, bowl / 2, fg);
                    g->fill_round_rect(x + t, y + t, w - 2 * t, bowl - 2 * t, (bowl - 2 * t) / 2, bg);
                    g->fill_rect(x, y + bowl / 2, w / 2 + 2, bowl / 2 + 2, bg);
                    g->thick_line(x + w - t / 2 - 1, y + bowl / 2 + 2, x + 2, y + h - t / 2, t, fg);
                    g->fill_rect(x, y + h - t, w, t, fg);
                }
                return w + 3;
            }
            case '3': {
                if (g) {
                    int half = (h + t) / 2, ybot = y + h - half;
                    g->fill_round_rect(x, y, w, half, half / 2, fg);
                    g->fill_round_rect(x + t, y + t, w - 2 * t, half - 2 * t, (half - 2 * t) / 2, bg);
                    g->fill_round_rect(x, ybot, w, half, half / 2, fg);
                    g->fill_round_rect(x + t, ybot + t, w - 2 * t, half - 2 * t, (half - 2 * t) / 2, bg);
                    g->fill_rect(x, y + t, w / 2 - 1, h - 2 * t, bg);
                    g->fill_rect(x + w / 3, y + (h - t) / 2, w / 3, t, fg);
                }
                return w + 3;
            }
            case '4': {
                if (g) {
                    int mid = y + (h * 65) / 100;
                    g->fill_rect(x + w - t - 3, y, t, h, fg);
                    g->fill_rect(x, mid, w, t, fg);
                    g->thick_line(x + w - t - 1, y + 2, x + 2, mid + t / 2, t, fg);
                }
                return w + 3;
            }
            case '5': {
                if (g) {
                    int ybot = y + (h * 42) / 100, hbot = h - (h * 42) / 100;
                    g->fill_round_rect(x, ybot, w, hbot, hbot / 2, fg);
                    g->fill_round_rect(x + t, ybot + t, w - 2 * t, hbot - 2 * t, (hbot - 2 * t) / 2, bg);
                    g->fill_rect(x, ybot, w / 2, hbot - t, bg);
                    g->fill_rect(x, y, w - 2, t, fg);
                    g->fill_rect(x, y, t, (h * 48) / 100, fg);
                }
                return w + 3;
            }
            case '6': {
                if (g) {
                    g->fill_round_rect(x, y, w, h, w / 2, fg);
                    g->fill_round_rect(x + t, y + t, w - 2 * t, h - 2 * t, (w - 2 * t) / 2, bg);
                    g->fill_rect(x + t, y + t, w, h / 2 - t, bg);
                    int hbot = (h * 58) / 100, ybot = y + h - hbot;
                    g->fill_round_rect(x, ybot, w, hbot, hbot / 2, fg);
                    g->fill_round_rect(x + t, ybot + t, w - 2 * t, hbot - 2 * t, (hbot - 2 * t) / 2, bg);
                }
                return w + 3;
            }
            case '7':
                if (g) {
                    g->fill_rect(x, y, w, t, fg);
                    g->thick_line(x + w - 2, y + t / 2, x + w / 4, y + h, t, fg);
                }
                return w + 3;
            case '8': {
                if (g) {
                    int half = (h + t) / 2, ybot = y + h - half;
                    g->fill_round_rect(x, y, w, half, half / 2, fg);
                    g->fill_round_rect(x + t, y + t, w - 2 * t, half - 2 * t, (half - 2 * t) / 2, bg);
                    g->fill_round_rect(x, ybot, w, half, half / 2, fg);
                    g->fill_round_rect(x + t, ybot + t, w - 2 * t, half - 2 * t, (half - 2 * t) / 2, bg);
                }
                return w + 3;
            }
            case '9': {
                if (g) {
                    g->fill_round_rect(x, y, w, h, w / 2, fg);
                    g->fill_round_rect(x + t, y + t, w - 2 * t, h - 2 * t, (w - 2 * t) / 2, bg);
                    g->fill_rect(x, y + h / 2, w - t, h / 2 - t, bg);
                    int htop = (h * 58) / 100;
                    g->fill_round_rect(x, y, w, htop, htop / 2, fg);
                    g->fill_round_rect(x + t, y + t, w - 2 * t, htop - 2 * t, (htop - 2 * t) / 2, bg);
                }
                return w + 3;
            }
            default:
                if (g) {
                    char s[2] = {ch, '\0'};
                    g->draw_text(s, x, y, 4, D::TOP_LEFT, fg);
                }
                return 16;
        }
    }

    int poppins_text(CANVAS *g, const char *s, int x, int y, int h, int t, uint16_t fg, uint16_t bg)
    {
        for (; *s; s++) {
            x += poppins(g, *s, x, y, h, t, fg, bg);
        }
        return x;
    }

    // ---------------------------------------------------------------- blueprint

    // The sketch's drawFlangedBearingSchematic(): one hardcoded flanged bearing housing, centred on (cx, cy)
    void draw_schematic(CANVAS &g, int cx, int cy, float s)
    {
        auto px = [s](float v) { return static_cast<int>(v * s); };

        g.fill_rect(cx - px(50), cy - px(60), px(100), px(120), C_WHITE);
        g.draw_rect(cx - px(50), cy - px(60), px(100), px(120), C_BLACK);

        // the narrower neck above and below the flange
        g.hline(cx - px(30), cy - px(30), px(60), C_BLACK);
        g.vline(cx - px(30), cy - px(60), px(30), C_BLACK);
        g.vline(cx + px(30), cy - px(60), px(30), C_BLACK);
        g.hline(cx - px(30), cy + px(30), px(60), C_BLACK);
        g.vline(cx - px(30), cy + px(30), px(30), C_BLACK);
        g.vline(cx + px(30), cy + px(30), px(30), C_BLACK);

        // dashed centre line of the bore
        for (int x = cx - px(70); x < cx + px(70); x += 10) {
            g.hline(x, cy, px(6), C_BLACK);
        }

        // dimension line with arrow heads, and its value
        const int dy = cy - px(70);
        g.hline(cx - px(50), dy, px(100), C_BLACK);
        g.fill_triangle(cx - px(50), dy, cx - px(45), dy - px(2) - 1, cx - px(45), dy + px(2) + 1, C_BLACK);
        g.fill_triangle(cx + px(50), dy, cx + px(45), dy - px(2) - 1, cx + px(45), dy + px(2) + 1, C_BLACK);
        g.draw_text("120.00 +/-0.02", cx, cy - px(80) - 2, s > 1.0f ? 2 : 1, D::MIDDLE_CENTER, C_BLACK);
    }
}


// ==================================================================== SCANNER_GUI

bool SCANNER_GUI::init()
{
    ready_ = display_ && canvas_.init();
    return ready_;
}


void SCANNER_GUI::redraw(int y0, int y1)
{
    if (ready_) {
        canvas_.render(*display_, y0, y1, [this](CANVAS &c) { draw(c); });
    }
}


void SCANNER_GUI::draw(CANVAS &c)
{
    c.fill_screen(C_BG);
    draw_header(c);

    if (has_order_) {
        if (enhanced_) {
            draw_enhanced(c, order_);
        } else {
            draw_normal(c, order_);
        }
    } else if (!has_banner_) {
        c.draw_text("PRESS THE BUTTON, THEN TAP A CARD", 240, 184, 2, D::MIDDLE_CENTER, C_OUTLINE);
    }

    if (has_banner_) {
        draw_banner(c);
    }
}


void SCANNER_GUI::draw_header(CANVAS &c)
{
    if (!c.in_band(0, HEADER_H)) {
        return;
    }
    c.fill_rect(0, 0, 480, HEADER_H, C_HEADER);
    c.draw_text("SCANNER UI", 20, 24, 4, D::MIDDLE_LEFT, C_TEXT);
    c.draw_text(clock_, 410, 24, 4, D::MIDDLE_RIGHT, C_TEXT);

    // battery icon (always full: the scanner runs on USB power)
    c.draw_rect(420, 15, 40, 18, C_TEXT);
    for (int i = 0; i < 4; i++) {
        c.fill_rect(422 + i * 9, 17, 8, 14, C_GREEN);
    }
    c.fill_rect(460, 20, 3, 8, C_TEXT);
}


// Split card: blueprint on the left, order details on the right
void SCANNER_GUI::draw_normal(CANVAS &c, const scanner_order_t &o)
{
    const int mx = 14, my = 60, mw = 452, mh = 244;
    const int mid = mx + mw / 2;
    const int box_w = mw / 2 - 20;
    char line[128];

    c.fill_round_rect(mx, my, mw, mh, 10, C_BLACK);
    c.fill_round_rect(mx + 2, my + 2, mw - 4, mh - 4, 8, C_PANEL_BG);

    // ---- left pane: blueprint between two strips
    c.fill_rect(mx + 2, my + 22, mid - mx - 2, mh - 44, C_WHITE);
    c.fill_round_rect(mx + 2, my + 2, mid - mx - 2, 20, 6, C_STRIP);
    c.fill_rect(mx + 2, my + 12, mid - mx - 2, 10, C_STRIP);
    c.hline(mx + 2, my + 22, mid - mx - 2, C_GREY);
    // "DWG: " only when the number doesn't already say it
    snprintf(line, sizeof(line), strncmp(o.dwg, "DWG", 3) == 0 ? "%s" : "DWG: %s", o.dwg[0] ? o.dwg : "-");
    text_fit(c, line, mx + 8, my + 12, 1, D::MIDDLE_LEFT, C_STRIP_TXT, 108);
    c.draw_text("SCALE 1:1 MM", mid - 8, my + 12, 1, D::MIDDLE_RIGHT, C_STRIP_TXT);

    draw_schematic(c, (mx + 2 + mid) / 2, my + mh / 2 + 10, 1.0f);

    c.fill_round_rect(mx + 2, my + mh - 22, mid - mx - 2, 20, 6, C_STRIP);
    c.fill_rect(mx + 2, my + mh - 22, mid - mx - 2, 10, C_STRIP);
    c.hline(mx + 2, my + mh - 22, mid - mx - 2, C_GREY);
    text_fit(c, o.part_no[0] ? o.part_no : "-", mx + 8, my + mh - 12, 1, D::MIDDLE_LEFT, C_STRIP_TXT, 100);
    text_fit(c, o.mat[0] ? o.mat : "-", mid - 8, my + mh - 12, 1, D::MIDDLE_RIGHT, C_STRIP_TXT, 100);

    c.vline(mid, my, mh, C_BLACK);
    c.vline(mid + 1, my, mh, C_BLACK);

    // ---- right pane
    const int px = mid + 10;
    int py = my + 10;

    // row 1: ORDER: ... LINE POS [3/6], then the id across the whole column
    c.draw_text("ORDER:", px, py, 2, D::TOP_LEFT, C_TEXT);
    c.fill_round_rect(mx + mw - 45, py - 2, 35, 20, 4, C_BLACK);
    if (o.finished) {
        snprintf(line, sizeof(line), "%d/%d", o.total, o.total);
    } else {
        snprintf(line, sizeof(line), "%d/%d", o.pos, o.total);
    }
    c.draw_text(line, mx + mw - 27, py + 8, 2, D::MIDDLE_CENTER, C_HEADER);
    c.draw_text("LINE POS", mx + mw - 50, py + 8, 1, D::MIDDLE_RIGHT, C_TEXT);

    // the id in the big "Poppins" digits when it fits, else the normal font
    if (poppins_text(nullptr, o.id, 0, 0, 26, 4, 0, 0) <= box_w) {
        poppins_text(&c, o.id, px, py + 20, 26, 4, C_CORAL, C_PANEL_BG);
    } else if (poppins_text(nullptr, o.id, 0, 0, 22, 4, 0, 0) <= box_w) {
        poppins_text(&c, o.id, px, py + 22, 22, 4, C_CORAL, C_PANEL_BG);
    } else {
        text_fit(c, o.id, px, py + 22, 4, D::TOP_LEFT, C_CORAL, box_w);
    }

    c.hline(px, py + 49, box_w, C_GREY);

    // progress along the route: done, next, to come
    py += 55;
    const int steps = std::max(o.total, 1);
    const int gap = 4;
    const int bar_w = (box_w - (steps - 1) * gap) / steps;
    for (int i = 1; i <= o.total; i++) {
        const bool done = o.finished || i < o.pos;
        const uint16_t fill = done ? C_GREEN : (i == o.pos ? C_CORAL : C_GREY);
        const uint16_t edge = (done || i == o.pos) ? C_BLACK : C_OUTLINE;
        const int x = px + (i - 1) * (bar_w + gap);
        c.fill_round_rect(x, py, bar_w, 6, 2, fill);
        c.draw_round_rect(x, py, bar_w, 6, 2, edge);
    }

    // row 2: part name (and "Box k of n")
    py += 15;
    c.fill_round_rect(px, py, box_w, 36, 4, C_WHITE);
    c.draw_round_rect(px, py, box_w, 36, 4, C_GREY);
    c.draw_text("PART NAME", px + 6, py + 5, 1, D::TOP_LEFT, C_LABEL);
    if (o.box[0]) {
        text_fit(c, o.box, px + box_w - 6, py + 5, 1, D::TOP_RIGHT, C_LABEL, box_w - 90);
    }
    text_fit(c, o.part, px + 6, py + 17, 2, D::TOP_LEFT, C_TEXT, box_w - 12);

    // row 3: what YES will record
    py += 45;
    c.fill_round_rect(px, py, box_w, 36, 4, C_MUSTARD_L);
    c.draw_round_rect(px, py, box_w, 36, 4, C_HEADER);
    c.draw_text("CONFIRM SCANNER STATION", px + 6, py + 5, 1, D::TOP_LEFT, C_DIM);
    if (o.finished) {
        strlcpy(line, "ROUTE COMPLETE", sizeof(line));
    } else {
        snprintf(line, sizeof(line), "%s: %s", o.event, o.process);
    }
    text_fit(c, line, px + 6, py + 17, 2, D::TOP_LEFT, C_TEXT, box_w - 26);
    c.fill_circle(px + box_w - 10, py + 24, 4, o.finished ? C_OUTLINE : C_GREEN);

    // row 4: dates
    py += 45;
    const int col2 = px + mw / 4 - 2;
    c.fill_round_rect(px, py, box_w, 36, 4, C_WHITE);
    c.draw_round_rect(px, py, box_w, 36, 4, C_GREY);
    c.draw_text("START DATE:", px + 6, py + 5, 1, D::TOP_LEFT, C_TEXT);
    c.draw_text(o.start, px + 6, py + 17, 2, D::TOP_LEFT, C_CORAL);
    c.vline(px + mw / 4 - 10, py + 4, 28, C_GREY);
    c.draw_text("DUE DATE:", col2, py + 5, 1, D::TOP_LEFT, C_TEXT);
    c.draw_text(o.end, col2, py + 17, 2, D::TOP_LEFT, C_CORAL);

    // row 5: customer and customer order number
    py += 42;
    // values 14 px below their label: Japanese glyphs are 16 px tall around the 8 px line
    c.draw_text("CUSTOMER:", px, py - 2, 1, D::TOP_LEFT, C_LABEL);
    text_fit(c, o.customer, px, py + 12, 1, D::TOP_LEFT, C_TEXT, mw / 4 - 14);
    c.vline(px + mw / 4 - 10, py - 2, 24, C_GREY);
    c.draw_text("ORDER NO:", col2, py - 2, 1, D::TOP_LEFT, C_LABEL);
    text_fit(c, o.order_number[0] ? o.order_number : "-", col2, py + 12, 1, D::TOP_LEFT, C_TEXT, px + box_w - col2);
}


// Big blueprint with a narrow spec column
void SCANNER_GUI::draw_enhanced(CANVAS &c, const scanner_order_t &o)
{
    const int mx = 8, my = 52, mw = 464, mh = 258;
    const int col_w = 130;
    const int mid = mx + mw - col_w;
    char line[128];

    c.fill_round_rect(mx, my, mw, mh, 10, C_BLACK);
    c.fill_round_rect(mx + 2, my + 2, mw - 4, mh - 4, 8, C_WHITE);

    // top bar: id | part name ... position and process
    c.fill_round_rect(mx + 2, my + 2, mw - 4, 28, 8, C_DARK);
    c.fill_rect(mx + 2, my + 16, mw - 4, 14, C_DARK);

    char process[40];
    fit(o.finished ? "COMPLETE" : o.process, 1, 110, process, sizeof(process));
    snprintf(line, sizeof(line), "POS: %d/%d * %s", o.finished ? o.total : o.pos, o.total, process);
    const int right_w = CANVAS::text_width(line, 1);
    c.draw_text(line, mx + mw - 10, my + 16, 1, D::MIDDLE_RIGHT, C_HEADER);

    text_fit(c, o.id, mx + 10, my + 16, 2, D::MIDDLE_LEFT, C_CORAL, 120);
    const int part_x = mx + 10 + std::min(CANVAS::text_width(o.id, 2), 120) + 8;
    snprintf(line, sizeof(line), "| %s", o.part);
    text_fit(c, line, part_x, my + 16, 2, D::MIDDLE_LEFT, C_GREY, (mx + mw - 10 - right_w) - part_x - 8);

    draw_schematic(c, (mx + 2 + mid) / 2, my + 30 + (mh - 32) / 2 + 12, 1.3f);

    // spec column
    c.fill_rect(mid + 2, my + 30, col_w - 4, mh - 40, C_STRIP);
    c.fill_round_rect(mid + 2, my + mh - 20, col_w - 4, 18, 8, C_STRIP);
    c.fill_rect(mid + 2, my + mh - 20, 10, 10, C_STRIP);
    c.vline(mid, my + 30, mh - 30, C_BLACK);
    c.vline(mid + 1, my + 30, mh - 30, C_BLACK);

    const int px = mid + 8;
    int py = my + 36;
    auto spec = [&](const char *label, const char *value, bool red) {
        c.draw_text(label, px, py, 1, D::TOP_LEFT, C_LABEL);
        text_fit(c, value[0] ? value : "-", px, py + 12, 1, D::TOP_LEFT, red ? C_CORAL : C_TEXT, col_w - 16);
        c.hline(px, py + 26, col_w - 18, C_GREY);
        py += 32;
    };
    spec("DRAWING NO.", o.dwg, false);
    spec("MATERIAL SPEC", o.mat, false);
    spec("PART NO.", o.part_no, true);
    spec("CUSTOMER", o.customer, false);
    spec("ORDER NO.", o.order_number, false);

    c.fill_round_rect(mid + 6, my + mh - 28, col_w - 12, 20, 4, C_DARK);
    c.draw_text("FULL BLUEPRINT", mid + 6 + (col_w - 12) / 2, my + mh - 18, 1, D::MIDDLE_CENTER, C_HEADER);
}


void SCANNER_GUI::draw_banner(CANVAS &c)
{
    const uint16_t color = banner_kind_ == banner_t::GOOD ? C_GREEN : banner_kind_ == banner_t::BAD ? C_CORAL : C_TEXT;

    c.fill_round_rect(70, 130, 340, 60, 12, C_BLACK);
    c.fill_round_rect(72, 132, 336, 56, 10, C_PANEL_BG);
    text_fit(c, banner_title_, 240, 150, 4, D::MIDDLE_CENTER, color, 328);
    text_fit(c, banner_subtitle_, 240, 175, 2, D::MIDDLE_CENTER, C_DIM, 328);
}


// ---------------------------------------------------------------- calls from the scanner task

void SCANNER_GUI::show_idle()
{
    has_order_ = false;
    has_banner_ = false;
    update_clock();
    redraw(0, CANVAS::HEIGHT);
}


void SCANNER_GUI::show_order(const scanner_order_t &order, bool enhanced)
{
    order_ = order;
    enhanced_ = enhanced;
    has_order_ = true;
    has_banner_ = false;
    update_clock();
    redraw(0, CANVAS::HEIGHT);
}


void SCANNER_GUI::show_banner(const char *title, const char *subtitle, banner_t kind)
{
    strlcpy(banner_title_, title, sizeof(banner_title_));
    strlcpy(banner_subtitle_, subtitle, sizeof(banner_subtitle_));
    banner_kind_ = kind;
    const bool was_shown = has_banner_;
    has_banner_ = true;
    // only the banner's rows change, unless the idle hint has to disappear too
    if (was_shown || has_order_) {
        redraw(128, 192);
    } else {
        redraw(HEADER_H, CANVAS::HEIGHT);
    }
}


void SCANNER_GUI::tick()
{
    if (update_clock()) {
        redraw(0, HEADER_H);
    }
}


// Clock text "HH:MM" from SNTP time (JST); "--:--" until the time is known. True if it changed.
bool SCANNER_GUI::update_clock()
{
    char text[8] = "--:--";
    time_t now = time(nullptr);
    if (now > 1700000000) {
        struct tm t;
        localtime_r(&now, &t);
        strftime(text, sizeof(text), "%H:%M", &t);
    }
    if (strcmp(text, clock_) == 0) {
        return false;
    }
    strlcpy(clock_, text, sizeof(clock_));
    return true;
}
