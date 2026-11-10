#include <stdio.h>

#include "text_ui.hpp"


namespace {
    const char *state_title(assign_state_t state)
    {
        switch (state) {
            case assign_state_t::ASSIGNED: return "LINKED";
            case assign_state_t::REFUSED:  return "REFUSED";
            case assign_state_t::BAD_KEY:  return "KEY ERROR";
            case assign_state_t::INVALID:  return "REQUEST ERROR";
            default:                       return "SERVER ERROR";
        }
    }

    const char *sort_name(setter_sort_t sort)
    {
        switch (sort) {
            case setter_sort_t::START: return "start date";
            case setter_sort_t::DUE:   return "due date";
            case setter_sort_t::PART:  return "part name";
            default:                   return "number";
        }
    }

    void print_row(int i, const setter_item_t &it, bool selected)
    {
        printf("%s%2d. %-12s due %s", selected ? ">" : " ", i + 1, it.code, it.due);
        if (it.boxes > 1) {
            printf("  box %d/%d", it.linked + 1, it.boxes);
        }
        printf("  %s\n", it.part);
    }
}


void TEXT_UI::show_message(const char *s)
{
    printf("\n... %s\n", s);
}


void TEXT_UI::show_error(const char *s)
{
    printf("\n!!! %s\n", s);
    printed_version_ = -1;   // print the list again afterwards
}


void TEXT_UI::show_list(const setter_list_t &list)
{
    if (list.version != printed_version_) {
        if (list.count == 0) {
            printf("\n--- No items need a tag ---\n");
        } else {
            printf("\n--- %d items needing a tag, sorted by %s ---\n", list.total, sort_name(list.sort));
        }
        for (int i = 0; i < list.count; i++) {
            print_row(i, list.items[i], i == list.selected);
        }
        if (list.count < list.total) {
            printf("(showing the first %d)\n", list.count);
        }
        printf("Enter a number or item code to link it.  w/s up/down  y link selected  r back  "
               "q details  e drawing  v view  f sort  n/p jump 8  j language\n");
    } else if (list.selected != printed_selected_ && list.count > 0) {
        print_row(list.selected, list.items[list.selected], true);
    }

    if (list.modal == setter_modal_t::DETAILS && printed_modal_ != setter_modal_t::DETAILS && list.count > 0) {
        const setter_item_t &it = list.items[list.selected];
        printf("\n=== %s  %s ===\n customer %s  order no. %s\n %d pieces, %d processes, ordered %s, due %s, box %d of %d\n",
               it.code, it.part, it.customer, it.order_number[0] ? it.order_number : "-", it.quantity, it.steps,
               it.ordered[0] ? it.ordered : "-", it.due, it.linked + 1, it.boxes);
    }

    if (list.banner) {
        printf("\n*** %s LINKED ***\n", list.banner);
    }

    printed_version_ = list.version;
    printed_selected_ = list.selected;
    printed_modal_ = list.modal;
}


void TEXT_UI::show_confirm_replace(const setter_item_t &item, const char *uid, const char *current_item)
{
    printf("\n??? Tag %s is linked to %s. Move it to %s?  y = yes, r = no\n", uid, current_item, item.code);
}


void TEXT_UI::show_waiting_for_card(const setter_item_t &item, int seconds)
{
    printf("\n>>> Tap a card for %s within %d s (r = cancel)\n", item.code, seconds);
}


void TEXT_UI::show_result(const setter_item_t &item, const assign_result_t &result)
{
    if (result.state == assign_state_t::ASSIGNED) {
        printf("\n>>> LINKED %s (box %d of %d), printing the sheet\n", item.code, result.linked, result.boxes);
        if (result.released[0]) {
            printf("    (moved from %s)\n", result.released);
        }
    } else {
        printf("\n>>> %s %s: %s\n", state_title(result.state), item.code, result.message);
    }
    printed_version_ = -1;
}


void TEXT_UI::show_picture_state(const setter_item_t &item, drawing_state_t state)
{
    switch (state) {
        case drawing_state_t::SHOWN:      printf("\n--- drawing of %s shown on the screen ---\n", item.code); break;
        case drawing_state_t::NO_DRAWING: printf("\n--- %s has no drawing ---\n", item.code); break;
        case drawing_state_t::UNREADABLE: printf("\n!!! the server could not read the drawing of %s\n", item.code); break;
        case drawing_state_t::BAD_KEY:    printf("\n!!! device key error (drawing)\n"); break;
        default:                          printf("\n!!! could not load the drawing of %s\n", item.code); break;
    }
}
