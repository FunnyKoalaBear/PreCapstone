#pragma once

#include "server_client.hpp"

// Everything the setter shows on screen. The UI implements this class (TEXT_UI is the simple built-in one).
// The setter task calls these methods, so they must draw and return quickly (no waiting, no HTTP).
class SETTER_UI
{
public:
    virtual ~SETTER_UI() = default;

    // short status text, e.g. "Loading items..."
    virtual void show_message(const char *text) = 0;

    // one page of items; rows are numbered 1..count, `offset` is the index of row 1 in the whole list
    virtual void show_list(const setter_item_t *items, int count, int offset, int total) = 0;

    // the operator picked `item`; ask them to tap a card within `seconds`
    virtual void show_waiting_for_card(const setter_item_t &item, int seconds) = 0;

    // what the server said about linking the card to `item`
    virtual void show_result(const setter_item_t &item, const assign_result_t &result) = 0;

    // something went wrong (network, bad row number, no card)
    virtual void show_error(const char *text) = 0;
};
