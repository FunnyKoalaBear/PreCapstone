#pragma once

#include "canvas.hpp"

// Everything the order screen shows about one scanned box. Text is UTF-8 (Japanese is drawn too).
struct scanner_order_t {
    char id[24];             // item code "2611-001-01" (or a sample id like "#2103")
    char part[96];           // part name
    int pos;                 // the step the next scan records, 1-based
    int total;               // steps on the route
    bool finished;           // route complete: every step shows as done
    char event[16];          // what YES records: ARRIVED, STARTED, FINISHED, SENT_OUT, RETURNED
    char process[64];        // the next step's process ("Milling")
    char start[8];           // "MM/DD" the order was registered
    char end[8];             // "MM/DD" due date
    char customer[96];
    char order_number[48];   // customer order number
    char dwg[64];            // drawing number / revision
    char part_no[48];        // part number
    char mat[64];            // material
    char box[32];            // "Box 2 of 3", or empty
};

// Colour of the banner title
enum class banner_t { GOOD, BAD, INFO };

// The Scanner's graphic UI, ported from scanner_ui.ino (TFT_eSPI) to the ESP-IDF display driver.
// Every call redraws the screen and returns; nothing here waits or talks to the network.
class SCANNER_GUI
{
public:
    explicit SCANNER_GUI(SCANNER_DISPLAY *display) : display_(display) {}

    // allocates the band buffer; if it fails every other call does nothing
    bool init();

    // header + empty grey workspace
    void show_idle();

    // the order card: split view, or the big blueprint view when enhanced
    void show_order(const scanner_order_t &order, bool enhanced);

    // message box over whatever is on screen (it stays until show_idle or show_order)
    void show_banner(const char *title, const char *subtitle, banner_t kind);

    // call about once a second: updates the clock
    void tick();

private:
    void redraw(int y0, int y1);
    void draw(CANVAS &c);
    void draw_header(CANVAS &c);
    void draw_normal(CANVAS &c, const scanner_order_t &o);
    void draw_enhanced(CANVAS &c, const scanner_order_t &o);
    void draw_banner(CANVAS &c);
    bool update_clock();

    SCANNER_DISPLAY *display_;
    CANVAS canvas_;
    bool ready_ = false;

    bool has_order_ = false;
    bool enhanced_ = false;
    scanner_order_t order_ = {};

    bool has_banner_ = false;
    banner_t banner_kind_ = banner_t::INFO;
    char banner_title_[32] = "";
    char banner_subtitle_[48] = "";

    char clock_[8] = "--:--";
};
