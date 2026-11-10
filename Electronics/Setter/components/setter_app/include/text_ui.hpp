#pragma once

#include "setter_ui.hpp"

// Serial monitor UI: prints every screen as text, so the keyboard (typed over the USB cable) can drive the setter.
// SETTER_GUI uses it to echo what it draws.
class TEXT_UI : public SETTER_UI
{
public:
    void show_message(const char *text) override;
    void show_list(const setter_list_t &list) override;
    void show_waiting_for_card(const setter_item_t &item, int seconds) override;
    void show_result(const setter_item_t &item, const assign_result_t &result) override;
    void show_confirm_replace(const setter_item_t &item, const char *uid, const char *current_item) override;
    void show_error(const char *text) override;

private:
    int printed_version_ = -1;   // the full list is printed again only when it changes
    int printed_selected_ = -1;
    setter_modal_t printed_modal_ = setter_modal_t::NONE;
};
