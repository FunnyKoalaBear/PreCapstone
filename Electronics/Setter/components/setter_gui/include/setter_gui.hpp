#pragma once

#include "freertos/FreeRTOS.h"

#include "setter_ui.hpp"
#include "text_ui.hpp"
#include "canvas.hpp"

// The Setter's graphic UI, ported from SetterUI.ino (TFT_eSPI) to the ESP-IDF display driver.
// Draws on the TFT and echoes everything to the serial monitor through TEXT_UI.
class SETTER_GUI : public SETTER_UI
{
public:
    explicit SETTER_GUI(SETTER_DISPLAY *display) : display_(display) {}

    // allocates the band buffer; false means use TEXT_UI only
    bool init();

    void show_message(const char *text) override;
    void show_list(const setter_list_t &list) override;
    void show_waiting_for_card(const setter_item_t &item, int seconds) override;
    void show_result(const setter_item_t &item, const assign_result_t &result) override;
    void show_confirm_replace(const setter_item_t &item, const char *uid, const char *current_item) override;
    void show_error(const char *text) override;
    void tick() override;
    void set_language(bool japanese) override;
    bool picture_sink(picture_sink_t &sink) override;
    void show_picture_state(const setter_item_t &item, drawing_state_t state) override;

private:
    enum class screen_t { MESSAGE, ERROR, LIST, CARD, RESULT, CONFIRM };

    void redraw(int y0, int y1);
    void draw(CANVAS &c);

    void draw_header(CANVAS &c);
    void draw_main(CANVAS &c);
    void draw_squares(CANVAS &c, int ox, int oy);
    void draw_sideways(CANVAS &c, int ox, int oy);
    void draw_table(CANVAS &c, int ox, int oy);
    void draw_view_selector(CANVAS &c);
    void draw_details(CANVAS &c, const setter_item_t &it);
    void draw_drawing(CANVAS &c, const setter_item_t &it);
    void draw_card_wait(CANVAS &c);
    void draw_result(CANVAS &c);
    void draw_confirm(CANVAS &c);
    void draw_pill_message(CANVAS &c, uint16_t color);

    bool update_clock();

    SETTER_DISPLAY *display_;
    CANVAS canvas_;
    TEXT_UI serial_;

    screen_t screen_ = screen_t::MESSAGE;
    setter_list_t list_ = {};
    setter_item_t item_ = {};
    assign_result_t result_ = {};
    int seconds_ = 0;
    char confirm_uid_[24] = "";
    char confirm_current_[24] = "";
    char message_[160] = "";
    char clock_[8] = "--:--";
    char banner_[48] = "";
    TickType_t banner_until_ = 0;
    const char *picture_note_ = nullptr;   // shown in the drawing area instead of "loading" (English key for tr())
};
