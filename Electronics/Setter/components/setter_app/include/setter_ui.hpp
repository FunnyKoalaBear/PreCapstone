#pragma once

#include "server_client.hpp"

// How the item list is laid out (cycled by the "view" button)
enum class setter_view_t { SQUARES, SIDEWAYS, TABLE };

// Sort order of the list (cycled by the "filter" button)
enum class setter_sort_t { NUMBER, START, DUE, PART };

// Screen shown on top of the list
enum class setter_modal_t { NONE, DETAILS, DRAWING };

// Everything needed to draw the list screen
struct setter_list_t {
    const setter_item_t *items;   // all loaded items, already sorted
    int count;                    // items loaded
    int total;                    // items needing a tag on the server (may be more than count)
    int selected;                 // highlighted item, 0..count-1
    int version;                  // changes whenever the items or their order change
    setter_view_t view;
    setter_sort_t sort;
    setter_modal_t modal;
    const char *banner;           // code of the item just linked (shown briefly as "<code> LINKED"), or nullptr
};

// Everything the setter shows. The setter task calls these, so they must draw and return quickly.
class SETTER_UI
{
public:
    virtual ~SETTER_UI() = default;

    // short status text, e.g. "Loading items..."
    virtual void show_message(const char *text) = 0;

    // the item list (or its details / drawing screen)
    virtual void show_list(const setter_list_t &list) = 0;

    // the operator picked `item`; ask them to tap a card within `seconds`
    virtual void show_waiting_for_card(const setter_item_t &item, int seconds) = 0;

    // what the server said about linking the card to `item`
    virtual void show_result(const setter_item_t &item, const assign_result_t &result) = 0;

    // something went wrong (network, bad row number, no card)
    virtual void show_error(const char *text) = 0;

    // called about once a second while idle (clock, hiding the banner)
    virtual void tick() {}

    // switch every screen between English and Japanese; the next show_... call draws in the new language
    virtual void set_language(bool japanese) {}
};
