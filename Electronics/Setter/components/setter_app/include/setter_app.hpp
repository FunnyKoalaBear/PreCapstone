#pragma once

#include "setter_ui.hpp"

class RFID;
class BUZZER;

// Starts the setter task: load items -> operator selects one -> tap a card -> link it on the server (which prints the sheet).
bool setter_app_start(SETTER_UI *ui, RFID *rfid, BUZZER *buzzer);

// Commands for the panel buttons, keyboard and touch. They only queue the command and return at once.
void setter_up();                           // select the previous item
void setter_down();                         // select the next item
void setter_prev_page();                    // jump 8 items back
void setter_next_page();                    // jump 8 items forward
void setter_link_selected();                // wait for a card for the selected item
void setter_back();                         // cancel the card wait, close details, or reload the list
void setter_toggle_details();               // details screen of the selected item on/off
void setter_toggle_drawing();               // drawing screen of the selected item on/off
void setter_next_view();                    // squares -> sideways -> table
void setter_next_sort();                    // number -> start date -> due date -> part name
void setter_pick(int row);                  // link the item at this 0-based position
void setter_pick_code(const char *code);    // link the item with this code, e.g. "2611-001-01"
void setter_reload();                       // load the list again from the server
void setter_toggle_language();              // English <-> Japanese
