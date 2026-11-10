#pragma once

class SCANNER_GUI;
class RFID;
class BUZZER;

// Starts the scanner task: button -> card (5 s) -> order card -> YES sends the scan / NO drops it.
bool scanner_app_start(SCANNER_GUI *gui, RFID *rfid, BUZZER *buzzer);

// Commands for the buttons and keyboard. They only queue the command and return at once.
void scanner_request_card();   // open the 5 s window to tap a card
void scanner_yes();            // confirm: send the scan to the server
void scanner_no();             // reject: nothing is sent
void scanner_enhance();        // big blueprint view on/off
void scanner_test_tap();       // show an order card without a card (UI test, nothing is sent)
