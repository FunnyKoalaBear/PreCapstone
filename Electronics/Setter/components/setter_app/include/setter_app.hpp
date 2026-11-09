#pragma once

#include "setter_ui.hpp"

class RFID;
class BUZZER;

// Starts the setter task: list items -> operator picks one -> tap a card -> link it on the server (which prints the sheet).
bool setter_app_start(SETTER_UI *ui, RFID *rfid, BUZZER *buzzer);

// Commands for the touch screen, panel buttons and keyboard. They only queue the command and return at once.
void setter_reload();
void setter_next_page();
void setter_prev_page();
void setter_pick(int row);                  // 0-based row on the current page
void setter_pick_code(const char *code);    // item code, e.g. "2611-001-01"
void setter_cancel();                       // stop waiting for a card (on the list it reloads)
