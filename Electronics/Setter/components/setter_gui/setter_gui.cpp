#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "setter_gui.hpp"


namespace {
    using D = CANVAS;

    // colours from SetterUI.ino
    constexpr uint16_t C_BG        = CANVAS::rgb(138, 138, 138);
    constexpr uint16_t C_HEADER_L  = CANVAS::rgb(245, 245, 245);
    constexpr uint16_t C_LOGO      = CANVAS::rgb(51, 51, 51);
    constexpr uint16_t C_NAVY      = CANVAS::rgb(30, 36, 128);
    constexpr uint16_t C_CARD_BG   = CANVAS::rgb(245, 245, 245);
    constexpr uint16_t C_WHITE     = 0xFFFF;
    constexpr uint16_t C_BLACK     = 0x0000;
    constexpr uint16_t C_CORAL     = CANVAS::rgb(250, 92, 92);
    constexpr uint16_t C_GEAR_DARK = CANVAS::rgb(52, 52, 52);
    constexpr uint16_t C_GEAR_MID  = CANVAS::rgb(96, 96, 96);
    constexpr uint16_t C_GREEN     = CANVAS::rgb(22, 163, 74);
    constexpr uint16_t C_ROW_DARK  = CANVAS::rgb(85, 85, 85);
    constexpr uint16_t C_ROW_LIGHT = CANVAS::rgb(125, 125, 125);

    constexpr int OX = 25, OY = 100;          // item area: 430 x 210
    constexpr int AREA_W = 430, AREA_H = 210;
    constexpr int SQUARES_PER_PAGE = 8;
    constexpr int TABLE_ROWS = 6;
    constexpr TickType_t BANNER_TICKS = pdMS_TO_TICKS(1600);

    const char *const FILTER_LABELS[] = {"FILTER: NUMERICAL", "FILTER: START DATE", "FILTER: DUE DATE", "FILTER: SCHEMATIC"};

    bool japanese = false;   // set by SETTER_GUI::set_language, read by tr()

    // English UI text -> Japanese. Keys are the exact English strings used below and sent by setter_app.
    struct translation_t {
        const char *en;
        const char *ja;
    };
    const translation_t TRANSLATIONS[] = {
        {"SETTER", "セッター"},
        {"ITEM %d OF %d", "品目 %d / %d"},
        {"FILTER: NUMERICAL", "並び順: 番号"},
        {"FILTER: START DATE", "並び順: 開始日"},
        {"FILTER: DUE DATE", "並び順: 納期"},
        {"FILTER: SCHEMATIC", "並び順: 品名"},
        {"START %s", "開始 %s"},
        {"DUE %s", "納期 %s"},
        {"ITEM", "品目"},
        {"PART", "品名"},
        {"START", "開始"},
        {"DUE", "納期"},
        {"BOX", "箱"},
        {"NO ITEMS NEED A TAG", "タグ待ちの品目はありません"},
        {"%s LINKED", "%s 紐付け完了"},
        {"ORDER: %s", "品目: %s"},
        {"CUSTOMER DETAILS:", "得意先情報:"},
        {"CUSTOMER NAME: ", "得意先名: "},
        {"CUSTOMER NAME: %s", "得意先名: %s"},
        {"CUSTOMER ORDER NUMBER: ", "客先注文番号: "},
        {"CUSTOMER ORDER NUMBER: %s", "客先注文番号: %s"},
        {"ORDER DETAILS", "受注内容"},
        {"%d PIECES", "%d 個"},
        {"DUE: %s", "納期: %s"},
        {"BOX %d OF %d", "箱 %d / %d"},
        {"PROCESS", "工程"},
        {"3 LINK   4 BACK", "3 紐付け   4 戻る"},
        {"DRAWING", "図面"},
        {"ON THE PRINTED", "印刷した"},
        {"WORK SHEET", "作業指示書の裏"},
        {"DRAWING NOT STORED ON THE SETTER", "図面はセッターに保存されていません"},
        {"IT IS PRINTED ON THE BACK OF THE WORK SHEET", "作業指示書の裏面に印刷されます"},
        {"TAP A CARD", "カードをかざしてください"},
        {"WITHIN %d S   -   4 BACK TO CANCEL", "%d 秒以内   -   4 でキャンセル"},
        {"LINKED", "紐付け完了"},
        {"REFUSED", "紐付けできません"},
        {"KEY ERROR", "キーエラー"},
        {"REQUEST ERROR", "リクエストエラー"},
        {"SERVER ERROR", "サーバーエラー"},
        {"PRINTING SHEET...", "指示書を印刷中..."},
        {"MOVED FROM %s", "%s から付け替えました"},
        {"CARD ALREADY IN USE", "このカードは使用中です"},
        {"TAG %s IS LINKED TO", "タグ %s の紐付け先"},
        {"MOVE IT TO %s?", "%s に付け替えますか?"},
        {"3 YES", "3 はい"},
        {"4 NO", "4 いいえ"},
        {"THE OLD LINK ENDS, ITS HISTORY IS KEPT", "元の紐付けは終了し、履歴は残ります"},
        {"Starting...", "起動中..."},
        {"Connecting WiFi...", "WiFi に接続中..."},
        {"Starting RFID reader...", "RFID リーダーを起動中..."},
        {"Loading items...", "品目を読み込み中..."},
        {"Linking...", "紐付け中..."},
        {"Server error. Press back to retry", "サーバーエラー。戻るボタンで再試行してください"},
        {"No card read", "カードを読み取れませんでした"},
        {"Cancelled", "キャンセルしました"},
        {"No such row", "その番号の品目はありません"},
        {"Item not found or already tagged", "品目が見つからないか、紐付け済みです"},
        {"Setter failed to start", "セッターを起動できませんでした"},
        {"Assigned, printing", "紐付け完了、印刷中"},
        {"Device key error", "端末キーのエラー"},
        {"Request error", "リクエストエラー"},
        {"Server error", "サーバーエラー"},
    };

    // The text in the current language (unknown text is shown as it is, e.g. the server's reasons)
    const char *tr(const char *en)
    {
        if (japanese) {
            for (const translation_t &t : TRANSLATIONS) {
                if (strcmp(t.en, en) == 0) {
                    return t.ja;
                }
            }
        }
        return en;
    }

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

    // "2026-12-01" -> "12/01"
    void mmdd(const char *iso, char *out, size_t size)
    {
        if (strlen(iso) >= 10) {
            snprintf(out, size, "%.2s/%.2s", iso + 5, iso + 8);
        } else {
            strlcpy(out, "--/--", size);
        }
    }

    // "2611-001-01" -> "-01" (item number) and "2611-001" (job number)
    const char *item_suffix(const char *code)
    {
        const char *dash = strrchr(code, '-');
        return dash ? dash : code;
    }

    void job_number(const char *code, char *out, size_t size)
    {
        strlcpy(out, code, size);
        char *dash = strrchr(out, '-');
        if (dash && dash != out) {
            *dash = '\0';
        }
    }

    // Splits text into lines at most max_w pixels wide, at spaces when possible (Japanese breaks anywhere)
    template <typename F>
    int wrap(const char *s, int font, int max_w, int max_lines, F &&line)
    {
        char buf[160];
        int row = 0;
        const char *p = s;
        while (*p && row < max_lines) {
            const char *end = p;
            const char *space = nullptr;
            for (const char *q = p; *q;) {
                const char *nq = next_utf8(q);
                const size_t len = std::min<size_t>(nq - p, sizeof(buf) - 1);
                memcpy(buf, p, len);
                buf[len] = '\0';
                if (CANVAS::text_width(buf, font) > max_w) {
                    break;
                }
                if (*q == ' ') {
                    space = q;
                }
                end = nq;
                q = nq;
            }
            if (*end && space && space > p) {
                end = space;   // the line is full: break at the last space
            }
            if (end == p) {
                end = next_utf8(p);   // one character wider than the line
            }
            const size_t len = std::min<size_t>(end - p, sizeof(buf) - 1);
            memcpy(buf, p, len);
            buf[len] = '\0';
            line(row++, buf);
            p = end;
            while (*p == ' ') {
                p++;
            }
        }
        return row;
    }

    // ---------------------------------------------------------------- logo and clock (from SetterUI.ino)

    void draw_logo(CANVAS &g)
    {
        const uint16_t c = C_LOGO;
        g.fill_rect(10, 14, 6, 31, c);
        g.thick_line(22, 22, 41, 45, 5, c);
        g.thick_line(41, 22, 22, 45, 5, c);
        g.fill_rect(50, 14, 28, 5, c);
        g.thick_line(75, 17, 53, 42, 6, c);
        g.fill_rect(50, 40, 28, 5, c);
        g.fill_round_rect(85, 14, 28, 31, 8, c);
        g.fill_round_rect(91, 19, 16, 21, 4, C_HEADER_L);
        g.fill_round_rect(120, 14, 28, 31, 8, c);
        g.fill_round_rect(126, 19, 16, 21, 4, C_HEADER_L);
        g.thick_line(136, 34, 150, 44, 5, c);
    }

    // One "Poppins" clock character; g == nullptr only measures. Returns the advance in pixels.
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
                    g->fill_rect(x + w - t - 2, y, t, h, fg);
                    g->fill_rect(x + 2, y, w - 4, t, fg);
                }
                return w + 3;
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

    // ---------------------------------------------------------------- picture placeholders

    // The sketch's drawImageCrop(): no drawing images on the device yet, so a framed placeholder
    // crop 0 = small square (shows the item number), 1 = details pane, 2 = full screen
    void draw_image(CANVAS &g, const setter_item_t &it, int x, int y, int w, int h, int crop)
    {
        if (!g.in_band(y, h)) {
            return;
        }
        g.fill_rect(x, y, w, h, C_WHITE);
        g.draw_rect(x, y, w, h, C_GEAR_MID);

        const char *num = item_suffix(it.code);
        if (crop == 0) {
            g.draw_rect(x + 2, y + 2, w - 4, h - 4, C_CORAL);
            int font = (CANVAS::text_width(num, 4) <= w - 8) ? 4 : 2;
            g.draw_text(num, x + w / 2, y + h / 2, font, D::MIDDLE_CENTER, C_BLACK);
        } else if (crop == 1) {
            g.draw_rect(x + 2, y + h / 4, w - 4, h / 2, C_GREEN);
            g.draw_text(tr("DRAWING"), x + w / 2, y + h / 2 - 30, 2, D::MIDDLE_CENTER, C_BLACK);
            g.draw_text(num, x + w / 2, y + h / 2, 4, D::MIDDLE_CENTER, C_BLACK);
            g.draw_text(tr("ON THE PRINTED"), x + w / 2, y + h / 2 + 30, 1, D::MIDDLE_CENTER, C_GEAR_MID);
            g.draw_text(tr("WORK SHEET"), x + w / 2, y + h / 2 + 42, 1, D::MIDDLE_CENTER, C_GEAR_MID);
        } else {
            g.draw_rect(x + 2, y + 2, w - 4, h - 4, C_NAVY);
            g.draw_text(it.code, x + w / 2, y + h / 2 - 30, 4, D::MIDDLE_CENTER, C_BLACK);
            g.draw_text(tr("DRAWING NOT STORED ON THE SETTER"), x + w / 2, y + h / 2 + 10, 2, D::MIDDLE_CENTER, C_GEAR_DARK);
            g.draw_text(tr("IT IS PRINTED ON THE BACK OF THE WORK SHEET"), x + w / 2, y + h / 2 + 35, 1, D::MIDDLE_CENTER, C_GEAR_MID);
        }
    }

    void draw_pie(CANVAS &g, int cx, int cy, int r, int steps)
    {
        if (!g.in_band(cy - r, 2 * r + 1)) {
            return;
        }
        g.fill_circle(cx, cy, r, C_GREEN);
        for (int i = 0; i < steps; i++) {
            float a = i * 2.0f * (float)M_PI / steps - (float)M_PI / 2.0f;
            g.thick_line(cx, cy, cx + r * cosf(a), cy + r * sinf(a), 3, C_WHITE);
        }
        g.fill_circle(cx, cy, r / 3 + 2, C_WHITE);
        char n[8];
        snprintf(n, sizeof(n), "%d", steps);
        g.draw_text(n, cx, cy, 2, D::MIDDLE_CENTER, C_BLACK);
    }
}


// ==================================================================== SETTER_GUI

bool SETTER_GUI::init()
{
    return display_ && canvas_.init();
}


void SETTER_GUI::redraw(int y0, int y1)
{
    canvas_.render(*display_, y0, y1, [this](CANVAS &c) { draw(c); });
}


void SETTER_GUI::draw(CANVAS &c)
{
    switch (screen_) {
        case screen_t::LIST:
            if (list_.count > 0 && list_.modal == setter_modal_t::DETAILS) {
                draw_details(c, list_.items[list_.selected]);
            } else if (list_.count > 0 && list_.modal == setter_modal_t::DRAWING) {
                draw_drawing(c, list_.items[list_.selected]);
            } else {
                draw_main(c);
            }
            break;
        case screen_t::CARD:
            draw_card_wait(c);
            break;
        case screen_t::RESULT:
            draw_result(c);
            break;
        case screen_t::CONFIRM:
            draw_confirm(c);
            break;
        case screen_t::ERROR:
            draw_pill_message(c, C_CORAL);
            break;
        default:
            draw_pill_message(c, C_NAVY);
            break;
    }
}


// ---------------------------------------------------------------- shared header

void SETTER_GUI::draw_header(CANVAS &c)
{
    if (!c.in_band(0, 54)) {
        return;
    }
    c.fill_rect(0, 0, 156, 54, C_HEADER_L);
    c.fill_rect(156, 0, 324, 54, C_NAVY);
    draw_logo(c);

    char status[48];
    strlcpy(status, tr("SETTER"), sizeof(status));
    if (screen_ == screen_t::LIST && list_.count > 0) {
        snprintf(status, sizeof(status), tr("ITEM %d OF %d"), list_.selected + 1, list_.count);
    }
    c.draw_text(status, 172, 27, 2, D::MIDDLE_LEFT, C_WHITE);

    int w = poppins_text(nullptr, clock_, 0, 0, 30, 5, 0, 0);
    poppins_text(&c, clock_, 466 - w, 12, 30, 5, C_WHITE, C_NAVY);
}


// ---------------------------------------------------------------- main list screen

void SETTER_GUI::draw_view_selector(CANVAS &c)
{
    const int x0 = 286, y0 = 62, bw = 61, h = 28;
    const int mode = static_cast<int>(list_.view);
    c.fill_round_rect(x0, y0, 184, h, 14, C_NAVY);
    for (int i = 0; i < 3; i++) {
        int x = x0 + i * bw;
        if (i == mode) {
            c.fill_round_rect(x + 1, y0 + 1, bw - 2, h - 2, 7, C_CORAL);
        }
        if (i > 0) {
            c.vline(x, y0 + 2, h - 4, C_WHITE);
        }
        if (i == 0) {
            for (int r = 0; r < 2; r++) {
                for (int col = 0; col < 3; col++) {
                    c.draw_rect(x + 16 + col * 9, y0 + 6 + r * 8, 6, 5, C_WHITE);
                }
            }
        } else if (i == 1) {
            c.draw_round_rect(x + 14, y0 + 5, 32, 8, 4, C_WHITE);
            c.draw_round_rect(x + 14, y0 + 15, 32, 8, 4, C_WHITE);
        } else {
            for (int r = 0; r < 3; r++) {
                c.hline(x + 15, y0 + 7 + r * 7, 30, C_WHITE);
            }
        }
    }
}


void SETTER_GUI::draw_squares(CANVAS &c, int ox, int oy)
{
    const int side = 94, gap = 10;
    const int first = (list_.selected / SQUARES_PER_PAGE) * SQUARES_PER_PAGE;
    const int last = std::min(first + SQUARES_PER_PAGE, list_.count);

    for (int i = first; i < last; i++) {
        const setter_item_t &it = list_.items[i];
        const int slot = i - first;
        const int x = ox + 3 + (slot % 4) * (side + gap), y = oy + 5 + (slot / 4) * 101;
        if (!c.in_band(y, side)) {
            continue;
        }
        const bool sel = (i == list_.selected);

        c.fill_round_rect(x, y, side, side, 12, sel ? C_CORAL : C_BLACK);
        c.fill_round_rect(x + 4, y + 4, side - 8, side - 8, 9, C_CARD_BG);

        char job[24];
        job_number(it.code, job, sizeof(job));
        if (sel) {
            c.fill_round_rect(x + 9, y + 7, side - 18, 17, 8, C_CORAL);
        }
        c.draw_text(job, x + side / 2, y + 15, 2, D::MIDDLE_CENTER, sel ? C_WHITE : C_BLACK);

        draw_image(c, it, x + side / 2 - 24, y + 55 - 24, 48, 48, 0);

        char start[8];
        mmdd(it.ordered, start, sizeof(start));
        c.draw_text(start, x + side / 2, y + 85, 1, D::MIDDLE_CENTER, C_BLACK);
        if (sel) {
            c.draw_round_rect(x + 1, y + 1, side - 2, side - 2, 11, C_CORAL);
        }
    }
}


void SETTER_GUI::draw_sideways(CANVAS &c, int ox, int oy)
{
    const int card_w = 136, card_h = 190, gap = 8;
    for (int slot = 0; slot < 3; slot++) {
        // fewer than 3 items: don't show the same item twice
        if ((list_.count == 1 && slot != 1) || (list_.count == 2 && slot == 0)) {
            continue;
        }
        const int idx = (list_.selected + slot - 1 + list_.count) % list_.count;
        const setter_item_t &it = list_.items[idx];
        const int x = ox + 3 + slot * (card_w + gap), y = oy + 8;
        const bool sel = (idx == list_.selected);

        c.fill_round_rect(x, y, card_w, card_h, 13, sel ? C_CORAL : C_BLACK);
        c.fill_round_rect(x + 4, y + 4, card_w - 8, card_h - 8, 9, C_CARD_BG);
        c.fill_round_rect(x + 7, y + 7, card_w - 14, 25, 8, sel ? C_CORAL : C_HEADER_L);
        c.draw_text(it.code, x + card_w / 2, y + 19, 2, D::MIDDLE_CENTER, sel ? C_WHITE : C_BLACK);

        draw_image(c, it, x + card_w / 2 - 35, y + 91 - 35, 70, 70, 0);

        char date[8], line[24];
        mmdd(it.ordered, date, sizeof(date));
        snprintf(line, sizeof(line), tr("START %s"), date);
        c.draw_text(line, x + card_w / 2, y + 145, 2, D::MIDDLE_CENTER, C_BLACK);
        mmdd(it.due, date, sizeof(date));
        snprintf(line, sizeof(line), tr("DUE %s"), date);
        c.draw_text(line, x + card_w / 2, y + 169, 2, D::MIDDLE_CENTER, C_BLACK);
        if (sel) {
            c.draw_round_rect(x + 1, y + 1, card_w - 2, card_h - 2, 12, C_CORAL);
        }
    }
}


void SETTER_GUI::draw_table(CANVAS &c, int ox, int oy)
{
    const int col_x[6] = {ox + 2, ox + 104, ox + 235, ox + 300, ox + 365, ox + 428};
    const char *headers[5] = {"ITEM", "PART", "START", "DUE", "BOX"};

    c.fill_rect(ox + 2, oy + 2, 426, 26, C_ROW_DARK);
    for (int col = 0; col < 5; col++) {
        c.draw_rect(col_x[col], oy + 2, col_x[col + 1] - col_x[col], 26, C_BG);
        c.draw_text(tr(headers[col]), (col_x[col] + col_x[col + 1]) / 2, oy + 15, 1, D::MIDDLE_CENTER, C_WHITE);
    }

    const int first = (list_.selected / TABLE_ROWS) * TABLE_ROWS;
    const int last = std::min(first + TABLE_ROWS, list_.count);
    for (int i = first; i < last; i++) {
        const setter_item_t &it = list_.items[i];
        const int y = oy + 29 + (i - first) * 29;
        if (!c.in_band(y, 28)) {
            continue;
        }
        const bool sel = (i == list_.selected);
        const uint16_t bg = sel ? C_CORAL : ((i % 2) ? C_ROW_LIGHT : C_ROW_DARK);
        c.fill_rect(ox + 2, y, 426, 28, bg);
        for (int col = 0; col < 5; col++) {
            c.draw_rect(col_x[col], y, col_x[col + 1] - col_x[col], 28, C_BG);
        }

        char part[24], start[8], due[8], box[28];
        fit(it.part, 1, col_x[2] - col_x[1] - 6, part, sizeof(part));
        mmdd(it.ordered, start, sizeof(start));
        mmdd(it.due, due, sizeof(due));
        snprintf(box, sizeof(box), "%d/%d", it.linked + 1, it.boxes);

        c.draw_text(it.code, (col_x[0] + col_x[1]) / 2, y + 14, 1, D::MIDDLE_CENTER, C_WHITE);
        c.draw_text(part, (col_x[1] + col_x[2]) / 2, y + 14, 1, D::MIDDLE_CENTER, C_WHITE);
        c.draw_text(start, (col_x[2] + col_x[3]) / 2, y + 14, 2, D::MIDDLE_CENTER, C_WHITE);
        c.draw_text(due, (col_x[3] + col_x[4]) / 2, y + 14, 2, D::MIDDLE_CENTER, C_WHITE);
        c.draw_text(box, (col_x[4] + col_x[5]) / 2, y + 14, 2, D::MIDDLE_CENTER, C_WHITE);
    }
}


void SETTER_GUI::draw_main(CANVAS &c)
{
    c.fill_screen(C_BG);
    draw_header(c);

    c.fill_round_rect(8, 62, 178, 28, 14, C_NAVY);
    c.draw_text(tr(FILTER_LABELS[static_cast<int>(list_.sort)]), 97, 76, 2, D::MIDDLE_CENTER, C_WHITE);
    draw_view_selector(c);

    c.fill_rect(OX, OY, AREA_W, AREA_H, C_BG);
    if (list_.count == 0) {
        c.draw_text(tr("NO ITEMS NEED A TAG"), 240, OY + AREA_H / 2, 4, D::MIDDLE_CENTER, C_WHITE);
    } else {
        switch (list_.view) {
            case setter_view_t::SIDEWAYS: draw_sideways(c, OX, OY); break;
            case setter_view_t::TABLE:    draw_table(c, OX, OY); break;
            default:                      draw_squares(c, OX, OY); break;
        }
    }

    if (banner_[0]) {
        c.fill_round_rect(70, 276, 340, 32, 16, C_BLACK);
        c.fill_round_rect(72, 278, 336, 28, 14, C_GREEN);
        char text[80];
        snprintf(text, sizeof(text), tr("%s LINKED"), banner_);
        c.draw_text(text, 240, 292, 2, D::MIDDLE_CENTER, C_WHITE);
    }
}


// ---------------------------------------------------------------- details and drawing screens

void SETTER_GUI::draw_details(CANVAS &c, const setter_item_t &it)
{
    char line[96], value[48], date[8];

    c.fill_screen(C_BLACK);
    c.fill_rect(4, 4, 472, 312, C_WHITE);

    c.fill_rect(4, 4, 472, 46, C_NAVY);
    snprintf(line, sizeof(line), tr("ORDER: %s"), it.code);
    c.draw_text(line, 16, 27, 4, D::MIDDLE_LEFT, C_WHITE);

    c.vline(190, 50, 266, C_BLACK);
    c.vline(191, 50, 266, C_BLACK);

    draw_image(c, it, 10, 60, 170, 240, 1);
    c.draw_text(tr("3 LINK   4 BACK"), 95, 309, 1, D::MIDDLE_CENTER, C_GEAR_MID);

    const int rx = 335, tx = 198;
    fit(it.part, 4, 270, value, sizeof(value));
    c.draw_text(value, rx, 70, 4, D::MIDDLE_CENTER, C_BLACK);

    c.draw_text(tr("CUSTOMER DETAILS:"), tx, 100, 2, D::MIDDLE_LEFT, C_BLACK);
    fit(it.customer, 2, 470 - tx - CANVAS::text_width(tr("CUSTOMER NAME: "), 2), value, sizeof(value));
    snprintf(line, sizeof(line), tr("CUSTOMER NAME: %s"), value);
    c.draw_text(line, tx, 120, 2, D::MIDDLE_LEFT, C_BLACK);
    fit(it.order_number[0] ? it.order_number : "-", 2, 470 - tx - CANVAS::text_width(tr("CUSTOMER ORDER NUMBER: "), 2),
        value, sizeof(value));
    snprintf(line, sizeof(line), tr("CUSTOMER ORDER NUMBER: %s"), value);
    c.draw_text(line, tx, 140, 2, D::MIDDLE_LEFT, C_BLACK);

    c.draw_text(tr("ORDER DETAILS"), rx, 175, 2, D::MIDDLE_CENTER, C_BLACK);
    c.hline(290, 185, 90, C_BLACK);

    const int pill_y = 205;
    c.fill_round_rect(205, pill_y - 12, 110, 24, 12, C_GREEN);
    snprintf(line, sizeof(line), tr("%d PIECES"), it.quantity);
    c.draw_text(line, 260, pill_y, 2, D::MIDDLE_CENTER, C_BLACK);

    c.fill_round_rect(325, pill_y - 12, 110, 24, 12, C_CORAL);
    mmdd(it.due, date, sizeof(date));
    snprintf(line, sizeof(line), tr("DUE: %s"), date);
    c.draw_text(line, 380, pill_y, 2, D::MIDDLE_CENTER, C_BLACK);

    if (it.boxes > 1) {
        snprintf(line, sizeof(line), tr("BOX %d OF %d"), it.linked + 1, it.boxes);
        c.draw_text(line, rx, 229, 1, D::MIDDLE_CENTER, C_BLACK);
    }

    c.draw_text(tr("PROCESS"), rx, 245, 2, D::MIDDLE_CENTER, C_BLACK);
    c.hline(300, 255, 70, C_BLACK);
    draw_pie(c, rx, 285, 26, it.steps);
}


void SETTER_GUI::draw_drawing(CANVAS &c, const setter_item_t &it)
{
    c.fill_screen(C_WHITE);
    draw_image(c, it, 10, 10, 460, 300, 2);
    c.draw_rect(4, 4, 472, 312, C_GEAR_DARK);
}


// ---------------------------------------------------------------- card, result and message screens

void SETTER_GUI::draw_card_wait(CANVAS &c)
{
    char line[64];
    c.fill_screen(C_BG);
    draw_header(c);

    c.fill_round_rect(40, 66, 400, 240, 16, C_NAVY);
    c.fill_round_rect(46, 104, 388, 196, 12, C_CARD_BG);
    c.draw_text(tr("TAP A CARD"), 240, 86, 4, D::MIDDLE_CENTER, C_WHITE);

    c.draw_text(item_.code, 240, 140, 4, D::MIDDLE_CENTER, C_BLACK);
    fit(item_.part, 2, 370, line, sizeof(line));
    c.draw_text(line, 240, 178, 2, D::MIDDLE_CENTER, C_GEAR_DARK);
    if (item_.boxes > 1) {
        snprintf(line, sizeof(line), tr("BOX %d OF %d"), item_.linked + 1, item_.boxes);
        c.draw_text(line, 240, 206, 2, D::MIDDLE_CENTER, C_BLACK);
    }
    snprintf(line, sizeof(line), tr("WITHIN %d S   -   4 BACK TO CANCEL"), seconds_);
    c.draw_text(line, 240, 272, 2, D::MIDDLE_CENTER, C_GEAR_MID);
}


void SETTER_GUI::draw_result(CANVAS &c)
{
    const bool ok = result_.state == assign_state_t::ASSIGNED;
    const uint16_t bg = ok ? C_GREEN : C_CORAL;
    const char *title = tr(ok ? "LINKED"
                         : result_.state == assign_state_t::REFUSED ? "REFUSED"
                         : result_.state == assign_state_t::BAD_KEY ? "KEY ERROR"
                         : result_.state == assign_state_t::INVALID ? "REQUEST ERROR" : "SERVER ERROR");

    c.fill_screen(C_BG);
    draw_header(c);
    c.fill_round_rect(40, 66, 400, 240, 16, C_BLACK);
    c.fill_round_rect(43, 69, 394, 234, 14, bg);

    c.draw_text(title, 240, 104, 4, D::MIDDLE_CENTER, C_WHITE);
    c.draw_text(item_.code, 240, 146, 4, D::MIDDLE_CENTER, C_WHITE);

    if (ok) {
        char line[96];
        if (result_.boxes > 1) {
            snprintf(line, sizeof(line), tr("BOX %d OF %d"), result_.linked, result_.boxes);
            c.draw_text(line, 240, 192, 2, D::MIDDLE_CENTER, C_WHITE);
        }
        if (result_.released[0]) {
            snprintf(line, sizeof(line), tr("MOVED FROM %s"), result_.released);
            c.draw_text(line, 240, 216, 2, D::MIDDLE_CENTER, C_WHITE);
        }
        c.draw_text(tr("PRINTING SHEET..."), 240, 240, 2, D::MIDDLE_CENTER, C_WHITE);
    } else {
        wrap(tr(result_.message), 2, 370, 4, [&](int row, const char *s) {
            c.draw_text(s, 240, 190 + row * 22, 2, D::MIDDLE_CENTER, C_WHITE);
        });
    }
}


// "This card is linked to X. Move it to Y?" with the yes / no buttons
void SETTER_GUI::draw_confirm(CANVAS &c)
{
    char line[96];
    c.fill_screen(C_BG);
    draw_header(c);

    c.fill_round_rect(40, 66, 400, 240, 16, C_NAVY);
    c.fill_round_rect(46, 104, 388, 196, 12, C_CARD_BG);
    c.draw_text(tr("CARD ALREADY IN USE"), 240, 86, 4, D::MIDDLE_CENTER, C_WHITE);

    snprintf(line, sizeof(line), tr("TAG %s IS LINKED TO"), confirm_uid_);
    c.draw_text(line, 240, 126, 2, D::MIDDLE_CENTER, C_GEAR_DARK);
    c.draw_text(confirm_current_, 240, 156, 4, D::MIDDLE_CENTER, C_CORAL);
    snprintf(line, sizeof(line), tr("MOVE IT TO %s?"), item_.code);
    c.draw_text(line, 240, 192, 2, D::MIDDLE_CENTER, C_BLACK);

    c.fill_round_rect(90, 218, 135, 32, 16, C_GREEN);
    c.draw_text(tr("3 YES"), 157, 234, 2, D::MIDDLE_CENTER, C_WHITE);
    c.fill_round_rect(255, 218, 135, 32, 16, C_CORAL);
    c.draw_text(tr("4 NO"), 322, 234, 2, D::MIDDLE_CENTER, C_WHITE);

    c.draw_text(tr("THE OLD LINK ENDS, ITS HISTORY IS KEPT"), 240, 278, 1, D::MIDDLE_CENTER, C_GEAR_MID);
}


// Header, grey body and one rounded pill with the message (navy = status, coral = error)
void SETTER_GUI::draw_pill_message(CANVAS &c, uint16_t color)
{
    c.fill_screen(C_BG);
    draw_header(c);

    int lines = 0;
    wrap(tr(message_), 2, 340, 3, [&](int, const char *) { lines++; });
    const int h = 40 + lines * 22;
    const int y = 185 - h / 2;
    c.fill_round_rect(60, y, 360, h, std::min(h / 2, 24), color);
    const int first_line_y = y + h / 2 - (lines - 1) * 11;   // lines 22 px apart, centred in the pill
    wrap(tr(message_), 2, 340, 3, [&](int row, const char *s) {
        c.draw_text(s, 240, first_line_y + row * 22, 2, D::MIDDLE_CENTER, C_WHITE);
    });
}


// ---------------------------------------------------------------- SETTER_UI calls

void SETTER_GUI::show_message(const char *text)
{
    serial_.show_message(text);
    strlcpy(message_, text, sizeof(message_));
    screen_ = screen_t::MESSAGE;
    update_clock();
    redraw(0, CANVAS::HEIGHT);
}


void SETTER_GUI::show_error(const char *text)
{
    serial_.show_error(text);
    strlcpy(message_, text, sizeof(message_));
    screen_ = screen_t::ERROR;
    update_clock();
    redraw(0, CANVAS::HEIGHT);
}


void SETTER_GUI::show_list(const setter_list_t &list)
{
    serial_.show_list(list);
    list_ = list;
    if (list.banner) {
        strlcpy(banner_, list.banner, sizeof(banner_));
        banner_until_ = xTaskGetTickCount() + BANNER_TICKS;
    }
    screen_ = screen_t::LIST;
    update_clock();
    redraw(0, CANVAS::HEIGHT);
}


void SETTER_GUI::show_waiting_for_card(const setter_item_t &item, int seconds)
{
    serial_.show_waiting_for_card(item, seconds);
    item_ = item;
    seconds_ = seconds;
    screen_ = screen_t::CARD;
    redraw(0, CANVAS::HEIGHT);
}


void SETTER_GUI::show_confirm_replace(const setter_item_t &item, const char *uid, const char *current_item)
{
    serial_.show_confirm_replace(item, uid, current_item);
    item_ = item;
    strlcpy(confirm_uid_, uid, sizeof(confirm_uid_));
    strlcpy(confirm_current_, current_item, sizeof(confirm_current_));
    screen_ = screen_t::CONFIRM;
    redraw(0, CANVAS::HEIGHT);
}


void SETTER_GUI::show_result(const setter_item_t &item, const assign_result_t &result)
{
    serial_.show_result(item, result);
    item_ = item;
    result_ = result;
    screen_ = screen_t::RESULT;
    redraw(0, CANVAS::HEIGHT);
}


void SETTER_GUI::set_language(bool ja)
{
    japanese = ja;
}


void SETTER_GUI::tick()
{
    // banner expired: redraw the list without it
    if (banner_[0] && (int32_t)(xTaskGetTickCount() - banner_until_) >= 0) {
        banner_[0] = '\0';
        if (screen_ == screen_t::LIST && list_.modal == setter_modal_t::NONE) {
            redraw(OY, CANVAS::HEIGHT);
        }
    }

    // minute changed: redraw the header only (the details and drawing screens have none)
    const bool has_header = !(screen_ == screen_t::LIST && list_.modal != setter_modal_t::NONE);
    if (update_clock() && has_header) {
        redraw(0, 54);
    }
}


// Clock text "HH:MM" from SNTP time (JST); "--:--" until the time is known. True if it changed.
bool SETTER_GUI::update_clock()
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
