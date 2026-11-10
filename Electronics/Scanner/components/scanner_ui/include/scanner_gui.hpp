#pragma once

#include "freertos/FreeRTOS.h"

#include "server_client.hpp"
#include "canvas.hpp"
#include "tft_gfx.hpp"

// scanner_ui.ino (TFT_eSPI) on the ESP-IDF display driver: blank screen, order modal
// (normal or enhanced blueprint) and the banner. Only the scanner task calls it.
class SCANNER_GUI
{
public:
    enum class banner_t { GOOD, BAD };   // green or coral title, like bannerColor in the sketch

    explicit SCANNER_GUI(SCANNER_DISPLAY *display) : display_(display) {}

    // allocates the band buffer; false means serial monitor only
    bool init();

    void show_blank();                              // header on the grey workspace (currentOrderIndex = -1)
    void show_order(const scan_preview_t &order);   // normal modal, like triggerRfidScan()
    void toggle_enhanced();                         // like handleEnhanceButton()

    // banner for `ms`, then the blank screen; keep_order leaves the modal under it, as YES / NO do
    void show_banner(const char *title, const char *subtitle, banner_t color, int ms, bool keep_order);

    // call every ~100 ms: banner timeout, clock
    void tick();

private:
    void redraw(int y0, int y1);
    void render_scene(TFT_GFX &gfx);
    void draw_header(TFT_GFX &gfx);
    void draw_normal_modal(TFT_GFX &gfx, const scan_preview_t &ord);
    void draw_enhanced_modal(TFT_GFX &gfx, const scan_preview_t &ord);
    bool update_clock();

    SCANNER_DISPLAY *display_;
    CANVAS canvas_;

    scan_preview_t order_ = {};
    bool has_order_ = false;    // currentOrderIndex >= 0
    bool enhanced_ = false;     // isEnhanced

    bool banner_ = false;       // showBanner
    char banner_title_[32] = "";
    char banner_subtitle_[64] = "";
    uint16_t banner_color_ = 0;
    TickType_t banner_until_ = 0;

    char clock_[8] = "--:--";
};
