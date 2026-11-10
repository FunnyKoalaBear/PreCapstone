#pragma once

class SCANNER_DISPLAY;

// The scanner flow from scanner_ui.ino: a card is read -> its order shows -> YES logs the scan,
// NO rejects it, E shows the blueprint. Inputs only queue a command, so they never wait on the network.

// Starts the scanner task. Pass nullptr if the display failed: everything then goes to the serial monitor.
bool scanner_ui_start(SCANNER_DISPLAY *display);

void scanner_ui_card(const char *uid);   // a card was read (the sketch's RFID tap)
void scanner_ui_no_card();               // the button's card window ran out
void scanner_ui_yes();                   // YES: log the scan on the server (handleYesButton)
void scanner_ui_no();                    // NO: reject it, nothing is sent (handleNoButton)
void scanner_ui_enhance();               // E: blueprint view on / off (handleEnhanceButton)
