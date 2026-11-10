#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "scanner_gui.hpp"


namespace {
    using G = TFT_GFX;

    // --- Colors (scanner_ui.ino)
    constexpr uint16_t C_BG       = G::color565(77, 77, 77);      // #4D4D4D Workspace Grey
    constexpr uint16_t C_HEADER   = G::color565(229, 171, 58);    // #E5AB3A Mustard Header
    constexpr uint16_t C_PANEL_BG = G::color565(247, 247, 247);   // #F7F7F7 Modal Right Pane
    constexpr uint16_t C_WHITE    = 0xFFFF;                       // Blueprint Left Pane
    constexpr uint16_t C_BLACK    = 0x0000;
    constexpr uint16_t C_CORAL    = G::color565(240, 84, 79);     // #F0544F Order ID / Dates
    constexpr uint16_t C_TEXT     = G::color565(9, 9, 9);         // #090909 Main Text
    constexpr uint16_t C_GREEN    = G::color565(5, 150, 105);     // emerald-600 Line Progress past
    constexpr uint16_t C_GREY     = G::color565(212, 212, 216);   // neutral-300 Line Progress future

    constexpr int HEADER_H = 48;


    // ---------------------------------------------------------------- text from the server

    // Copies text as ASCII (each Japanese character becomes '?'), upper-cased if asked,
    // and cuts it to max_w pixels with ".."
    void fit(const char *s, int font, int max_w, char *out, size_t size, bool upper = false)
    {
        size_t n = 0;
        for (const unsigned char *p = (const unsigned char *)s; *p && n + 1 < size; p++) {
            if (*p < 0x80) {
                out[n++] = upper ? toupper(*p) : *p;
            } else if (*p >= 0xC0) {
                out[n++] = '?';
            }
        }
        out[n] = '\0';
        if (G::text_width(out, font) <= max_w) {
            return;
        }
        n = std::min(n, size - 3);   // room for ".."
        out[n] = '\0';
        while (n > 0 && G::text_width(out, font) + G::text_width("..", font) > max_w) {
            out[--n] = '\0';
        }
        strlcat(out, "..", size);
    }

    const char *or_dash(const char *s)
    {
        return s[0] ? s : "-";
    }

    // "2026-12-01" -> "12/01", like the sketch's "06/30"
    void mmdd(const char *iso, char *out, size_t size)
    {
        if (strlen(iso) >= 10) {
            snprintf(out, size, "%.2s/%.2s", iso + 5, iso + 8);
        } else {
            strlcpy(out, "--/--", size);
        }
    }

    // ord.dwg: "DWG: DW-1-B" (drawing number and revision)
    void dwg_text(const scan_preview_t &o, char *out, size_t size)
    {
        if (!o.drawing[0]) {
            strlcpy(out, "-", size);
        } else if (o.revision[0]) {
            snprintf(out, size, "%s-%s", o.drawing, o.revision);
        } else {
            strlcpy(out, o.drawing, size);
        }
    }

    // ord.station: "STN-03: CUTTING", the sketch's format (the event goes on the label row)
    void station_text(const scan_preview_t &o, int max_w, char *out, size_t size)
    {
        char prefix[16], process[64];
        snprintf(prefix, sizeof(prefix), "STN-%02d", o.step);
        if (!o.process[0]) {
            strlcpy(out, prefix, size);
            return;
        }
        strlcat(prefix, ": ", sizeof(prefix));
        fit(o.process, 2, max_w - G::text_width(prefix, 2), process, sizeof(process), true);
        snprintf(out, size, "%s%s", prefix, process);
    }

    // "SENT_OUT" -> "SENT OUT": what a YES records at that station
    void event_text(const scan_preview_t &o, char *out, size_t size)
    {
        strlcpy(out, o.event_type, size);
        for (char *p = out; *p; p++) {
            if (*p == '_') {
                *p = ' ';
            }
        }
    }


    // ---------------------------------------------------------------- vector drawing primitives

    void fill_thick_line(G &gfx, float x0, float y0, float x1, float y1, int thickness, uint16_t color)
    {
        float dx = x1 - x0, dy = y1 - y0;
        float len = sqrtf(dx * dx + dy * dy);
        if (len < 0.1f) return;
        float nx = -dy / len * (thickness * 0.5f);
        float ny =  dx / len * (thickness * 0.5f);
        gfx.fill_triangle(roundf(x0 + nx), roundf(y0 + ny), roundf(x0 - nx), roundf(y0 - ny), roundf(x1 - nx), roundf(y1 - ny), color);
        gfx.fill_triangle(roundf(x0 + nx), roundf(y0 + ny), roundf(x1 - nx), roundf(y1 - ny), roundf(x1 + nx), roundf(y1 + ny), color);
    }


    // ---------------------------------------------------------------- Poppins vector font (scanner_ui.ino)

    // One character; gfx == nullptr only measures. Returns the advance in pixels.
    int draw_poppins_char(G *gfx, char ch, int x, int y, int h, int t, uint16_t fg, uint16_t bg)
    {
        int w = (h * 72) / 100;
        switch (ch) {
            case ' ': return h / 3;
            case ':':
                if (gfx) {
                    gfx->fill_circle(x + t/2 + 1, y + h/3, t/2 + 1, fg);
                    gfx->fill_circle(x + t/2 + 1, y + (h*3)/4, t/2 + 1, fg);
                }
                return t + 5;
            case '-':   // not in scanner_ui.ino; same glyph as SetterUI.ino, item codes need it
                if (gfx) {
                    gfx->fill_rect(x + 2, y + (h - t) / 2, w / 2, t, fg);
                }
                return w / 2 + 5;
            case '#': {
                int wHash = (h * 80) / 100;
                if (gfx) {
                    fill_thick_line(*gfx, x + wHash*0.35f, y + 1, x + wHash*0.20f, y + h - 1, t - 1, fg);
                    fill_thick_line(*gfx, x + wHash*0.75f, y + 1, x + wHash*0.60f, y + h - 1, t - 1, fg);
                    gfx->fill_rect(x + 1, y + h/3 - t/2, wHash - 2, t - 1, fg);
                    gfx->fill_rect(x + 1, y + (h*2)/3 - t/2, wHash - 2, t - 1, fg);
                }
                return wHash + 3;
            }
            case '/': {
                int wSlash = (h * 50) / 100;
                if (gfx) {
                    fill_thick_line(*gfx, x + wSlash - 2, y - 1, x + 2, y + h + 2, t, fg);
                }
                return wSlash + 3;
            }
            case 'O': case '0':
                if (gfx) {
                    gfx->fill_round_rect(x, y, w, h, w / 2, fg);
                    gfx->fill_round_rect(x + t, y + t, w - 2*t, h - 2*t, (w - 2*t) / 2, bg);
                }
                return w + 3;
            case 'R': {
                if (gfx) {
                    int bowlH = (h * 56) / 100;
                    gfx->fill_round_rect(x, y, w, bowlH, bowlH / 2, fg);
                    gfx->fill_round_rect(x + t, y + t, w - 2*t, bowlH - 2*t, (bowlH - 2*t) / 2, bg);
                    gfx->fill_rect(x + t, y + t, w/2 - t, bowlH - 2*t, bg);
                    gfx->fill_rect(x, y, t, h, fg);
                    gfx->fill_rect(x + t, y, w/2 - t, t, fg);
                    gfx->fill_rect(x + t, y + bowlH - t, w/2 - t, t, fg);
                    fill_thick_line(*gfx, x + w/2, y + bowlH - 2, x + w - 2, y + h, t, fg);
                }
                return w + 3;
            }
            case 'D':
                if (gfx) {
                    gfx->fill_round_rect(x, y, w, h, h / 2, fg);
                    gfx->fill_round_rect(x + t, y + t, w - 2*t, h - 2*t, (h - 2*t) / 2, bg);
                    gfx->fill_rect(x, y, w / 2, h, bg);
                    gfx->fill_rect(x, y, t, h, fg);
                    gfx->fill_rect(x, y, w / 2, t, fg);
                    gfx->fill_rect(x, y + h - t, w / 2, t, fg);
                }
                return w + 3;
            case 'E':
                if (gfx) {
                    gfx->fill_rect(x, y, t, h, fg);
                    gfx->fill_rect(x, y, w, t, fg);
                    gfx->fill_rect(x, y + (h - t)/2, w - 2, t, fg);
                    gfx->fill_rect(x, y + h - t, w, t, fg);
                }
                return w + 3;
            case '1':
                if (gfx) {
                    gfx->fill_rect(x + w - t - 2, y, t, h, fg);
                    gfx->fill_rect(x + 2, y, w - 4, t, fg);
                }
                return w + 3;
            case '2': {
                if (gfx) {
                    int bowlH = (h * 52) / 100;
                    gfx->fill_round_rect(x, y, w, bowlH, bowlH / 2, fg);
                    gfx->fill_round_rect(x + t, y + t, w - 2*t, bowlH - 2*t, (bowlH - 2*t)/2, bg);
                    gfx->fill_rect(x, y + bowlH/2, w/2 + 2, bowlH/2 + 2, bg);
                    fill_thick_line(*gfx, x + w - t/2 - 1, y + bowlH/2 + 2, x + 2, y + h - t/2, t, fg);
                    gfx->fill_rect(x, y + h - t, w, t, fg);
                }
                return w + 3;
            }
            case '3': {
                if (gfx) {
                    int hHalf = (h + t) / 2, yBot = y + h - hHalf;
                    gfx->fill_round_rect(x, y, w, hHalf, hHalf / 2, fg);
                    gfx->fill_round_rect(x + t, y + t, w - 2*t, hHalf - 2*t, (hHalf - 2*t)/2, bg);
                    gfx->fill_round_rect(x, yBot, w, hHalf, hHalf / 2, fg);
                    gfx->fill_round_rect(x + t, yBot + t, w - 2*t, hHalf - 2*t, (hHalf - 2*t)/2, bg);
                    gfx->fill_rect(x, y + t, w/2 - 1, h - 2*t, bg);
                    gfx->fill_rect(x + w/3, y + (h - t)/2, w/3, t, fg);
                }
                return w + 3;
            }
            case '4': {
                if (gfx) {
                    int midY = y + (h * 65) / 100;
                    gfx->fill_rect(x + w - t - 3, y, t, h, fg);
                    gfx->fill_rect(x, midY, w, t, fg);
                    fill_thick_line(*gfx, x + w - t - 1, y + 2, x + 2, midY + t/2, t, fg);
                }
                return w + 3;
            }
            case '5': {
                if (gfx) {
                    int yBot = y + (h * 42) / 100, hBot = h - (h * 42) / 100;
                    gfx->fill_round_rect(x, yBot, w, hBot, hBot / 2, fg);
                    gfx->fill_round_rect(x + t, yBot + t, w - 2*t, hBot - 2*t, (hBot - 2*t)/2, bg);
                    gfx->fill_rect(x, yBot, w/2, hBot - t, bg);
                    gfx->fill_rect(x, y, w - 2, t, fg);
                    gfx->fill_rect(x, y, t, (h * 48) / 100, fg);
                }
                return w + 3;
            }
            case '6': {
                if (gfx) {
                    gfx->fill_round_rect(x, y, w, h, w / 2, fg);
                    gfx->fill_round_rect(x + t, y + t, w - 2*t, h - 2*t, (w - 2*t)/2, bg);
                    gfx->fill_rect(x + t, y + t, w, h/2 - t, bg);
                    int hBot = (h * 58) / 100, yBot = y + h - hBot;
                    gfx->fill_round_rect(x, yBot, w, hBot, hBot / 2, fg);
                    gfx->fill_round_rect(x + t, yBot + t, w - 2*t, hBot - 2*t, (hBot - 2*t)/2, bg);
                }
                return w + 3;
            }
            case '7':
                if (gfx) {
                    gfx->fill_rect(x, y, w, t, fg);
                    fill_thick_line(*gfx, x + w - 2, y + t/2, x + w/4, y + h, t, fg);
                }
                return w + 3;
            case '8': {
                if (gfx) {
                    int hHalf = (h + t) / 2, yBot = y + h - hHalf;
                    gfx->fill_round_rect(x, y, w, hHalf, hHalf / 2, fg);
                    gfx->fill_round_rect(x + t, y + t, w - 2*t, hHalf - 2*t, (hHalf - 2*t)/2, bg);
                    gfx->fill_round_rect(x, yBot, w, hHalf, hHalf / 2, fg);
                    gfx->fill_round_rect(x + t, yBot + t, w - 2*t, hHalf - 2*t, (hHalf - 2*t)/2, bg);
                }
                return w + 3;
            }
            case '9': {
                if (gfx) {
                    gfx->fill_round_rect(x, y, w, h, w / 2, fg);
                    gfx->fill_round_rect(x + t, y + t, w - 2*t, h - 2*t, (w - 2*t)/2, bg);
                    gfx->fill_rect(x, y + h/2, w - t, h/2 - t, bg);
                    int hTop = (h * 58) / 100;
                    gfx->fill_round_rect(x, y, w, hTop, hTop / 2, fg);
                    gfx->fill_round_rect(x + t, y + t, w - 2*t, hTop - 2*t, (hTop - 2*t)/2, bg);
                }
                return w + 3;
            }
            default: {
                char s[2] = {ch, '\0'};
                if (!gfx) {
                    return G::text_width(s, 4);
                }
                // font 4 fallback, keeping the caller's text colour and datum
                const uint16_t old_fg = gfx->text_color(), old_bg = gfx->text_bg_color();
                const G::datum_t old_datum = gfx->text_datum();
                gfx->set_text_color(fg, bg);
                gfx->set_text_datum(G::TL_DATUM);
                const int adv = gfx->draw_char(ch, x, y, 4);
                gfx->set_text_color(old_fg, old_bg);
                gfx->set_text_datum(old_datum);
                return adv;
            }
        }
    }

    int draw_poppins_text(G *gfx, const char *str, int x, int y, int h, int t, uint16_t fg, uint16_t bg)
    {
        int curX = x;
        for (const char *p = str; *p; p++) {
            curX += draw_poppins_char(gfx, *p, curX, y, h, t, fg, bg);
        }
        return curX;
    }


    // ---------------------------------------------------------------- schematic (hardcoded, as in the sketch)

    void draw_flanged_bearing_schematic(G &gfx, int ox, int oy, float scale)
    {
        int w = 226 * scale;
        int h = 244 * scale;

        // Draw base shape (simplified)
        int cx = ox + w/2;
        int cy = oy + h/2;

        gfx.draw_rect(cx - 50*scale, cy - 60*scale, 100*scale, 120*scale, C_BLACK);
        gfx.fill_rect(cx - 50*scale + 1, cy - 60*scale + 1, 100*scale - 2, 120*scale - 2, C_WHITE);

        gfx.fill_rect(cx - 30*scale, cy - 60*scale, 60*scale, 30*scale, C_WHITE);
        gfx.draw_fast_hline(cx - 30*scale, cy - 30*scale, 60*scale, C_BLACK);
        gfx.draw_fast_vline(cx - 30*scale, cy - 60*scale, 30*scale, C_BLACK);
        gfx.draw_fast_vline(cx + 30*scale, cy - 60*scale, 30*scale, C_BLACK);

        gfx.fill_rect(cx - 30*scale, cy + 30*scale, 60*scale, 30*scale, C_WHITE);
        gfx.draw_fast_hline(cx - 30*scale, cy + 30*scale, 60*scale, C_BLACK);
        gfx.draw_fast_vline(cx - 30*scale, cy + 30*scale, 30*scale, C_BLACK);
        gfx.draw_fast_vline(cx + 30*scale, cy + 30*scale, 30*scale, C_BLACK);

        // Center bore line
        for (int i = cx - 70*scale; i < cx + 70*scale; i+=10) {
            gfx.draw_fast_hline(i, cy, 6*scale, C_BLACK);
        }

        // Dimensions (Mockup)
        gfx.draw_line(cx - 50*scale, cy - 70*scale, cx + 50*scale, cy - 70*scale, C_BLACK);
        gfx.fill_triangle(cx - 50*scale, cy - 70*scale, cx - 45*scale, cy - 72*scale, cx - 45*scale, cy - 68*scale, C_BLACK);
        gfx.fill_triangle(cx + 50*scale, cy - 70*scale, cx + 45*scale, cy - 72*scale, cx + 45*scale, cy - 68*scale, C_BLACK);

        gfx.set_text_color(C_BLACK, C_WHITE);
        gfx.set_text_datum(G::MC_DATUM);
        if(scale > 1.0) gfx.draw_string("120.00 +/-0.02", cx, cy - 80*scale, 2);
        else gfx.draw_string("120.00 +/-0.02", cx, cy - 80*scale, 1);
    }
}


// ==================================================================== UI rendering

void SCANNER_GUI::draw_header(G &gfx)
{
    gfx.fill_rect(0, 0, 480, 48, C_HEADER);
    gfx.set_text_color(C_TEXT, C_HEADER);
    gfx.set_text_datum(G::ML_DATUM);
    gfx.draw_string("SCANNER UI", 20, 24, 4);

    gfx.set_text_datum(G::MR_DATUM);
    gfx.draw_string(clock_, 410, 24, 4);   // the sketch shows a fixed "16:20"

    // Battery icon
    gfx.draw_rect(420, 15, 40, 18, C_TEXT);
    gfx.fill_rect(422, 17, 8, 14, C_GREEN);
    gfx.fill_rect(431, 17, 8, 14, C_GREEN);
    gfx.fill_rect(440, 17, 8, 14, C_GREEN);
    gfx.fill_rect(449, 17, 8, 14, C_GREEN);
    gfx.fill_rect(460, 20, 3, 8, C_TEXT);
}


void SCANNER_GUI::draw_normal_modal(G &gfx, const scan_preview_t &ord)
{
    int mx = 14, my = 60, mw = 452, mh = 244;
    char text[96], value[64];

    // Modal Base
    gfx.fill_round_rect(mx, my, mw, mh, 10, C_BLACK);
    gfx.fill_round_rect(mx + 2, my + 2, mw - 4, mh - 4, 8, C_PANEL_BG);

    // Split Line
    int midX = mx + mw / 2;
    gfx.draw_fast_vline(midX, my, mh, C_BLACK);
    gfx.draw_fast_vline(midX+1, my, mh, C_BLACK); // 2px border

    // --- LEFT PANE: Blueprint ---
    gfx.fill_rect(mx + 2, my + 2, midX - mx - 2, mh - 4, C_WHITE);

    // Top Strip
    gfx.fill_rect(mx + 2, my + 2, midX - mx - 2, 20, G::color565(245, 245, 245));
    gfx.draw_fast_hline(mx + 2, my + 22, midX - mx - 2, C_GREY);
    gfx.set_text_color(G::color565(38,38,38));
    gfx.set_text_datum(G::ML_DATUM);
    dwg_text(ord, value, sizeof(value));
    snprintf(text, sizeof(text), "DWG: %s", value);
    fit(text, 1, midX - 8 - G::text_width("SCALE 1:1  MM", 1) - 12 - (mx + 8), value, sizeof(value));
    gfx.draw_string(value, mx + 8, my + 12, 1);
    gfx.set_text_datum(G::MR_DATUM);
    gfx.draw_string("SCALE 1:1  MM", midX - 8, my + 12, 1);

    // Schematic
    draw_flanged_bearing_schematic(gfx, mx + 2, my + 22, 1.0);

    // Bottom Strip (no tolerance on the server: the quantity goes in ord.tol's place)
    gfx.fill_rect(mx + 2, my + mh - 22, midX - mx - 2, 20, G::color565(245, 245, 245));
    gfx.draw_fast_hline(mx + 2, my + mh - 22, midX - mx - 2, C_GREY);
    gfx.set_text_datum(G::ML_DATUM);
    snprintf(text, sizeof(text), "QTY: %d PCS", ord.quantity);
    const int tol_w = gfx.draw_string(text, mx + 8, my + mh - 12, 1);
    gfx.set_text_datum(G::MR_DATUM);
    snprintf(text, sizeof(text), "MAT: %s", or_dash(ord.material));
    fit(text, 1, midX - 8 - (mx + 8 + tol_w + 12), value, sizeof(value));
    gfx.draw_string(value, midX - 8, my + mh - 12, 1);


    // --- RIGHT PANE: Telemetry ---
    int px = midX + 10;
    int py = my + 10;

    // LINE POS pill grows only if the step count needs more room than "3/6"
    char pos[16];
    snprintf(pos, sizeof(pos), "%d/%d", ord.step, ord.steps);
    const int pill_w = std::max(35, G::text_width(pos, 2) + 8);
    const int pill_x = mx + mw - 10 - pill_w;

    // Row 1: Order ID & Line Pos
    gfx.set_text_color(C_TEXT, C_PANEL_BG);
    gfx.set_text_datum(G::TL_DATUM);
    gfx.draw_string("ORDER:", px, py, 2);

    // item code in Poppins, 26 px tall like "#2103", smaller only if it would run into the pill
    int ph = 26;
    while (ph > 14 && draw_poppins_text(nullptr, ord.item, 0, 0, ph, 0, 0, 0) > pill_x - 6 - px) {
        ph--;
    }
    draw_poppins_text(&gfx, ord.item, px, py + 20, ph, std::max(2, (ph * 4 + 13) / 26), C_CORAL, C_PANEL_BG);

    gfx.set_text_datum(G::TR_DATUM);
    gfx.draw_string("LINE POS", mx + mw - 10, py, 1);
    gfx.fill_round_rect(pill_x, py + 15, pill_w, 20, 4, C_BLACK);
    gfx.set_text_color(C_HEADER, C_BLACK);
    gfx.set_text_datum(G::MC_DATUM);
    gfx.draw_string(pos, pill_x + (pill_w + 1) / 2, py + 25, 2);

    gfx.draw_fast_hline(px, py + 45, mw/2 - 20, C_GREY);

    // Line Progress Bar (32 px segments as in the sketch; narrower for routes over 6 steps)
    py += 55;
    int barW = ord.steps <= 6 ? 32 : std::max(3, 216 / std::max(ord.steps, 1) - 4);
    for(int i=1; i<=ord.steps; i++) {
        uint16_t bColor = (i < ord.step) ? C_GREEN : (i == ord.step ? C_CORAL : C_GREY);
        uint16_t outline = (i <= ord.step) ? C_BLACK : G::color565(160,160,160);
        gfx.fill_round_rect(px + (i-1)*(barW+4), py, barW, 6, 2, bColor);
        gfx.draw_round_rect(px + (i-1)*(barW+4), py, barW, 6, 2, outline);
    }

    // Row 2: Part Name
    py += 15;
    gfx.fill_round_rect(px, py, mw/2 - 20, 36, 4, C_WHITE);
    gfx.draw_round_rect(px, py, mw/2 - 20, 36, 4, C_GREY);
    gfx.set_text_color(G::color565(115,115,115), C_WHITE);
    gfx.set_text_datum(G::TL_DATUM);
    gfx.draw_string("PART NAME", px + 6, py + 6, 1);
    gfx.set_text_color(C_TEXT, C_WHITE);
    fit(or_dash(ord.part), 2, mw/2 - 20 - 12, value, sizeof(value));
    gfx.draw_string(value, px + 6, py + 20, 2);

    // Row 3: Confirm Station (the step a YES records)
    py += 45;
    gfx.fill_round_rect(px, py, mw/2 - 20, 36, 4, G::color565(247, 235, 206)); // Light mustard
    gfx.draw_round_rect(px, py, mw/2 - 20, 36, 4, C_HEADER);
    gfx.set_text_color(G::color565(64,64,64), G::color565(247, 235, 206));
    gfx.draw_string("CONFIRM SCANNER STATION", px + 6, py + 6, 1);
    event_text(ord, value, sizeof(value));
    gfx.set_text_color(C_TEXT, G::color565(247, 235, 206));
    if (value[0]) {   // not in the sketch: STARTED / FINISHED etc., right of the label
        gfx.set_text_datum(G::TR_DATUM);
        gfx.draw_string(value, px + mw/2 - 20 - 6, py + 6, 1);
        gfx.set_text_datum(G::TL_DATUM);
    }
    station_text(ord, (mw/2 - 35 - 4 - 4) - 6, value, sizeof(value));
    gfx.draw_string(value, px + 6, py + 20, 2);
    gfx.fill_circle(px + mw/2 - 35, py + 18, 4, C_GREEN);

    // Row 4: Dates (start = order registered, end = due date)
    py += 45;
    gfx.fill_round_rect(px, py, mw/2 - 20, 36, 4, C_WHITE);
    gfx.draw_round_rect(px, py, mw/2 - 20, 36, 4, C_GREY);

    gfx.set_text_color(C_TEXT, C_WHITE);
    gfx.draw_string("START DATE:", px + 6, py + 6, 1);
    gfx.set_text_color(C_CORAL, C_WHITE);
    mmdd(ord.ordered, value, sizeof(value));
    gfx.draw_string(value, px + 6, py + 20, 2);

    gfx.draw_fast_vline(px + mw/4 - 10, py + 4, 28, C_GREY);

    gfx.set_text_color(C_TEXT, C_WHITE);
    gfx.draw_string("END DATE:", px + mw/4 - 2, py + 6, 1);
    gfx.set_text_color(C_CORAL, C_WHITE);
    mmdd(ord.due, value, sizeof(value));
    gfx.draw_string(value, px + mw/4 - 2, py + 20, 2);

    // Row 5: Cust / Order no. (no engineer on the server: the customer's order number takes its place)
    py += 42;
    gfx.set_text_color(G::color565(115,115,115), C_PANEL_BG);
    gfx.draw_string("CUSTOMER:", px, py, 1);
    gfx.set_text_color(C_TEXT, C_PANEL_BG);
    fit(or_dash(ord.customer), 1, mw/4 - 10 - 6, value, sizeof(value));
    gfx.draw_string(value, px, py + 10, 1);

    gfx.draw_fast_vline(px + mw/4 - 10, py, 20, C_GREY);

    gfx.set_text_color(G::color565(115,115,115), C_PANEL_BG);
    gfx.draw_string("ORDER NO:", px + mw/4 - 2, py, 1);
    gfx.set_text_color(C_TEXT, C_PANEL_BG);
    fit(or_dash(ord.order_number), 1, (mx + mw - 8) - (px + mw/4 - 2), value, sizeof(value));
    gfx.draw_string(value, px + mw/4 - 2, py + 10, 1);
}


void SCANNER_GUI::draw_enhanced_modal(G &gfx, const scan_preview_t &ord)
{
    int mx = 8, my = 52, mw = 464, mh = 258;
    char text[96], value[64];

    gfx.fill_round_rect(mx, my, mw, mh, 10, C_BLACK);
    gfx.fill_round_rect(mx + 2, my + 2, mw - 4, mh - 4, 8, C_WHITE);

    // Top Header
    gfx.fill_rect(mx + 2, my + 2, mw - 4, 28, G::color565(20,20,20));
    gfx.set_text_color(C_CORAL, G::color565(20,20,20));
    gfx.set_text_datum(G::ML_DATUM);
    const int id_w = gfx.draw_string(ord.item, mx + 10, my + 16, 2);

    char stn[24];
    snprintf(stn, sizeof(stn), "POS: %d/%d * STN-%02d", ord.step, ord.steps, ord.step);
    const int part_x = std::max(mx + 65, mx + 10 + id_w + 8);   // the sketch's x + 65 fits "#2103", not "2611-001-01"
    snprintf(text, sizeof(text), "| %s", or_dash(ord.part));
    fit(text, 2, (mx + mw - 10 - G::text_width(stn, 1) - 10) - part_x, value, sizeof(value));
    gfx.set_text_color(C_GREY, G::color565(20,20,20));
    gfx.draw_string(value, part_x, my + 16, 2);

    gfx.set_text_datum(G::MR_DATUM);
    gfx.set_text_color(C_HEADER, G::color565(20,20,20));
    gfx.draw_string(stn, mx + mw - 10, my + 16, 1);

    // Split Line
    int rightColW = 130;
    int midX = mx + mw - rightColW;
    gfx.draw_fast_vline(midX, my + 30, mh - 30, C_BLACK);
    gfx.draw_fast_vline(midX+1, my + 30, mh - 30, C_BLACK);

    // Large Schematic
    draw_flanged_bearing_schematic(gfx, mx + 2, my + 30, 1.4);

    // Right Info Column
    int px = midX + 6;
    int py = my + 36;
    gfx.fill_rect(midX + 2, my + 30, rightColW - 4, mh - 32, G::color565(245, 245, 245));

    auto drawSpec = [&](int &yPos, const char *label, const char *val, bool isRed = false) {
        char shown[48];
        fit(or_dash(val), 1, rightColW - 16, shown, sizeof(shown));
        gfx.set_text_color(G::color565(115,115,115), G::color565(245, 245, 245));
        gfx.set_text_datum(G::TL_DATUM);
        gfx.draw_string(label, px, yPos, 1);
        gfx.set_text_color(isRed ? C_CORAL : C_TEXT, G::color565(245, 245, 245));
        gfx.draw_string(shown, px, yPos + 12, 1);
        gfx.draw_fast_hline(px, yPos + 26, rightColW - 16, C_GREY);
        yPos += 32;
    };

    char dwg[48], qty[24];
    dwg_text(ord, dwg, sizeof(dwg));
    snprintf(qty, sizeof(qty), "%d PCS", ord.quantity);
    drawSpec(py, "DRAWING NO.", dwg);
    drawSpec(py, "MATERIAL SPEC", ord.material);
    drawSpec(py, "QUANTITY", qty, true);           // TOLERANCE in the sketch
    drawSpec(py, "CUSTOMER", ord.customer);
    drawSpec(py, "ORDER NO.", ord.order_number);   // LEAD ENGINEER in the sketch

    gfx.fill_round_rect(px, my + mh - 26, rightColW - 16, 20, 4, G::color565(20,20,20));
    gfx.set_text_color(C_HEADER, G::color565(20,20,20));
    gfx.set_text_datum(G::MC_DATUM);
    gfx.draw_string("FULL BLUEPRINT", px + (rightColW - 16)/2, my + mh - 16, 1);
}


void SCANNER_GUI::render_scene(G &gfx)
{
    gfx.fill_screen(C_BG);
    draw_header(gfx);

    if (has_order_) {
        if (enhanced_) {
            draw_enhanced_modal(gfx, order_);
        } else {
            draw_normal_modal(gfx, order_);
        }
    }

    if (banner_) {
        gfx.fill_round_rect(70, 130, 340, 60, 12, C_BLACK);
        gfx.fill_round_rect(72, 132, 336, 56, 10, G::color565(247, 247, 247));

        gfx.set_text_color(banner_color_, G::color565(247, 247, 247));
        gfx.set_text_datum(G::MC_DATUM);
        gfx.draw_string(banner_title_, 240, 150, 4);

        gfx.set_text_color(G::color565(64,64,64), G::color565(247, 247, 247));
        gfx.draw_string(banner_subtitle_, 240, 175, 2);
    }
}


// ==================================================================== calls from the scanner task

bool SCANNER_GUI::init()
{
    return display_ && canvas_.init();
}


// Like updateDisplay(): renderScene() once per 16-row band of rows y0..y1-1
void SCANNER_GUI::redraw(int y0, int y1)
{
    canvas_.render(*display_, y0, y1, [this](CANVAS &c) {
        TFT_GFX gfx(c);
        render_scene(gfx);
    });
}


void SCANNER_GUI::show_blank()
{
    banner_ = false;
    has_order_ = false;
    enhanced_ = false;
    update_clock();
    redraw(0, CANVAS::HEIGHT);
}


void SCANNER_GUI::show_order(const scan_preview_t &order)
{
    banner_ = false;
    enhanced_ = false;
    order_ = order;
    has_order_ = true;
    update_clock();
    redraw(0, CANVAS::HEIGHT);
}


void SCANNER_GUI::toggle_enhanced()
{
    if (has_order_) {
        enhanced_ = !enhanced_;
        redraw(0, CANVAS::HEIGHT);
    }
}


void SCANNER_GUI::show_banner(const char *title, const char *subtitle, banner_t color, int ms, bool keep_order)
{
    fit(title, 4, 330, banner_title_, sizeof(banner_title_));
    fit(subtitle, 2, 330, banner_subtitle_, sizeof(banner_subtitle_));
    banner_color_ = (color == banner_t::GOOD) ? C_GREEN : C_CORAL;
    banner_ = true;
    enhanced_ = false;
    has_order_ = has_order_ && keep_order;
    banner_until_ = xTaskGetTickCount() + pdMS_TO_TICKS(ms);
    update_clock();
    redraw(0, CANVAS::HEIGHT);
}


void SCANNER_GUI::tick()
{
    // like loop(): the banner times out, then back to the blank grey screen
    if (banner_ && (int32_t)(xTaskGetTickCount() - banner_until_) >= 0) {
        show_blank();
        return;
    }
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
