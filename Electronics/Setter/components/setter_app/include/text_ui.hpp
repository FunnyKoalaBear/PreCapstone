#pragma once

#include <stdint.h>

#include "setter_ui.hpp"

class SETTER_DISPLAY;

// Simple built-in UI: prints every screen to the serial monitor and draws plain text on the TFT.
// Stand-in until the real UI is ready; display may be nullptr (serial only).
class TEXT_UI : public SETTER_UI
{
public:
    explicit TEXT_UI(SETTER_DISPLAY *display) : display_(display) {}

    void show_message(const char *text) override;
    void show_list(const setter_item_t *items, int count, int offset, int total) override;
    void show_waiting_for_card(const setter_item_t &item, int seconds) override;
    void show_result(const setter_item_t &item, const assign_result_t &result) override;
    void show_error(const char *text) override;

private:
    void clear(uint16_t color);
    void text(int x, int y, const char *s, int scale, uint16_t fg, uint16_t bg);
    void centered(int y, const char *s, int scale, uint16_t fg, uint16_t bg);
    void wrapped(int y, const char *s, uint16_t fg, uint16_t bg);

    SETTER_DISPLAY *display_;
};
