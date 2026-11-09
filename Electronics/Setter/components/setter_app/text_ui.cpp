#include <stdio.h>
#include <string.h>

#include "text_ui.hpp"
#include "setter_display.hpp"


namespace {
    constexpr int SCALE = 2;                                   // 16 px letters
    constexpr int CHAR_W = 8 * SCALE;
    constexpr int LINE_H = 24;
    constexpr int MARGIN = 8;
    constexpr int MAX_CHARS = (SETTER_DISPLAY::WIDTH - 2 * MARGIN) / CHAR_W;   // 29

    constexpr uint16_t WHITE = SETTER_DISPLAY::WHITE;
    constexpr uint16_t BLACK = SETTER_DISPLAY::BLACK;
    constexpr uint16_t GREEN = 0x0400;   // RGB565 dark green
    constexpr uint16_t RED   = 0xA000;   // RGB565 dark red

    // The 8x8 font is ASCII only: each Japanese (UTF-8) character becomes one '?'
    void to_ascii(const char *src, char *dst, size_t max_chars)
    {
        size_t n = 0;
        for (const unsigned char *p = (const unsigned char *)src; *p && n < max_chars; p++) {
            if (*p < 0x80) {
                dst[n++] = (*p >= 0x20) ? *p : ' ';
            } else if (*p >= 0xC0) {
                dst[n++] = '?';   // first byte of a multi-byte character; the rest (0x80-0xBF) are skipped
            }
        }
        dst[n] = '\0';
    }

    const char *state_title(assign_state_t state)
    {
        switch (state) {
            case assign_state_t::ASSIGNED:     return "LINKED";
            case assign_state_t::REFUSED:      return "REFUSED";
            case assign_state_t::BAD_KEY:      return "KEY ERROR";
            case assign_state_t::INVALID:      return "REQUEST ERROR";
            default:                           return "SERVER ERROR";
        }
    }
}


void TEXT_UI::clear(uint16_t color)
{
    if (display_) {
        display_->fill_screen(color);
    }
}

void TEXT_UI::text(int x, int y, const char *s, int scale, uint16_t fg, uint16_t bg)
{
    if (!display_) {
        return;
    }
    char line[64];
    to_ascii(s, line, (SETTER_DISPLAY::WIDTH - x) / (8 * scale));
    display_->draw_text_at(line, x, y, scale, fg, bg);
}

void TEXT_UI::centered(int y, const char *s, int scale, uint16_t fg, uint16_t bg)
{
    char line[64];
    to_ascii(s, line, SETTER_DISPLAY::WIDTH / (8 * scale));
    int x = (SETTER_DISPLAY::WIDTH - (int)strlen(line) * 8 * scale) / 2;
    text(x, y, line, scale, fg, bg);
}

// Long text split into lines of MAX_CHARS, breaking at spaces when possible (up to 6 lines)
void TEXT_UI::wrapped(int y, const char *s, uint16_t fg, uint16_t bg)
{
    char all[160];
    to_ascii(s, all, sizeof(all) - 1);
    const char *p = all;

    for (int row = 0; *p && row < 6; row++) {
        size_t len = strlen(p);
        size_t take = len;
        if (len > (size_t)MAX_CHARS) {
            take = MAX_CHARS;
            while (take > 0 && p[take] != ' ') {
                take--;
            }
            if (take == 0) {
                take = MAX_CHARS;
            }
        }
        char line[MAX_CHARS + 1];
        memcpy(line, p, take);
        line[take] = '\0';
        centered(y + row * LINE_H, line, SCALE, fg, bg);
        p += take;
        while (*p == ' ') {
            p++;
        }
    }
}


void TEXT_UI::show_message(const char *s)
{
    printf("\n... %s\n", s);
    clear(WHITE);
    centered(SETTER_DISPLAY::HEIGHT / 2 - 8, s, SCALE, BLACK, WHITE);
}


void TEXT_UI::show_error(const char *s)
{
    printf("\n!!! %s\n", s);
    clear(RED);
    wrapped(SETTER_DISPLAY::HEIGHT / 2 - LINE_H, s, WHITE, RED);
}


void TEXT_UI::show_list(const setter_item_t *items, int count, int offset, int total)
{
    char line[160];   // room for every field; text() cuts it to the screen width

    if (total == 0) {
        printf("\n--- No items need a tag ---\n");
    } else {
        printf("\n--- Items needing a tag (%d-%d of %d) ---\n", offset + 1, offset + count, total);
    }
    for (int i = 0; i < count; i++) {
        const setter_item_t &it = items[i];
        printf("%2d. %-12s due %s", i + 1, it.code, it.due);
        if (it.boxes > 1) {
            printf("  box %d/%d", it.linked + 1, it.boxes);
        }
        printf("  %s\n", it.part);
    }
    printf("Type a row number or an item code, then Enter.  n = next page, p = previous, r = reload\n");

    if (!display_) {
        return;
    }
    clear(WHITE);

    if (total == 0) {
        snprintf(line, sizeof(line), "No items need a tag");
    } else {
        snprintf(line, sizeof(line), "Need a tag: %d-%d of %d", offset + 1, offset + count, total);
    }
    text(MARGIN, MARGIN, line, SCALE, WHITE, BLACK);

    for (int i = 0; i < count; i++) {
        const setter_item_t &it = items[i];
        const char *due = strlen(it.due) >= 10 ? it.due + 5 : it.due;   // "2026-12-01" -> "12-01"
        if (it.boxes > 1) {
            snprintf(line, sizeof(line), "%d %s %s %d/%d %s", i + 1, it.code, due, it.linked + 1, it.boxes, it.part);
        } else {
            snprintf(line, sizeof(line), "%d %s %s %s", i + 1, it.code, due, it.part);
        }
        text(MARGIN, MARGIN + (i + 2) * LINE_H, line, SCALE, BLACK, WHITE);
    }

    text(MARGIN, MARGIN + 11 * LINE_H, "No./code+Enter  n p r", SCALE, BLACK, WHITE);
}


void TEXT_UI::show_waiting_for_card(const setter_item_t &item, int seconds)
{
    char line[64];
    printf("\n>>> Tap a card for %s within %d s (c = cancel)\n", item.code, seconds);

    clear(WHITE);
    centered(30, "Tap a card for", SCALE, BLACK, WHITE);
    centered(80, item.code, 4, BLACK, WHITE);
    centered(140, item.part, SCALE, BLACK, WHITE);
    if (item.boxes > 1) {
        snprintf(line, sizeof(line), "Box %d of %d", item.linked + 1, item.boxes);
        centered(175, line, SCALE, BLACK, WHITE);
    }
    snprintf(line, sizeof(line), "Within %d s   c = cancel", seconds);
    centered(260, line, SCALE, BLACK, WHITE);
}


void TEXT_UI::show_result(const setter_item_t &item, const assign_result_t &result)
{
    char line[64];
    const bool ok = result.state == assign_state_t::ASSIGNED;
    const uint16_t bg = ok ? GREEN : RED;

    if (ok) {
        printf("\n>>> LINKED %s (box %d of %d), printing the sheet\n", item.code, result.linked, result.boxes);
    } else {
        printf("\n>>> %s %s: %s\n", state_title(result.state), item.code, result.message);
    }

    clear(bg);
    centered(40, state_title(result.state), 4, WHITE, bg);
    centered(110, item.code, 3, WHITE, bg);

    if (ok) {
        if (result.boxes > 1) {
            snprintf(line, sizeof(line), "Box %d of %d", result.linked, result.boxes);
            centered(170, line, SCALE, WHITE, bg);
        }
        centered(230, "Printing sheet...", SCALE, WHITE, bg);
    } else {
        wrapped(170, result.message, WHITE, bg);
    }
}
