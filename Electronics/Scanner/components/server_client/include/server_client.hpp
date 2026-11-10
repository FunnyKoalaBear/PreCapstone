#pragma once

#include <stddef.h>

// What the server did with a scan (POST /api/scan/)
enum class scan_state_t {
    RECORDED,       // 200: an event was saved
    FINISHED,       // 200: the route was already complete, nothing saved
    DUPLICATE,      // 200: same tag scanned again within a few seconds, nothing saved
    UNASSIGNED,     // 404: the tag is not linked to an item
    BAD_KEY,        // 401: wrong key or device inactive
    INVALID,        // 400: firmware sent bad data
    SERVER_ERROR,   // 5xx, timeout, no WiFi: worth a retry
};

struct scan_result_t {
    scan_state_t state;
    char item[24];      // item code, e.g. "2611-001-01" (empty on errors)
    char process[48];   // process name of the saved event (RECORDED only)
    char event[16];     // STARTED, FINISHED, ... (RECORDED only)
};

// What the server knows about a tag (GET /api/tag/<uid>/); nothing is saved by asking
enum class lookup_state_t {
    OPEN,           // linked to an item that still has steps to do
    FINISHED,       // linked, route already complete
    UNASSIGNED,     // 404: the tag is not linked to an item
    BAD_KEY,        // 401: wrong key or device inactive
    SERVER_ERROR,   // anything else (network, 5xx, bad JSON)
};

// Text fields are UTF-8 and may be Japanese (3 bytes per character)
struct tag_info_t {
    lookup_state_t state;
    char item[24];           // item code, e.g. "2611-001-01"
    char part[96];
    char part_number[48];
    char drawing[64];        // "DWG-1001 / A"
    char material[64];
    char customer[96];
    char order_number[48];   // customer order number
    char ordered[12];        // "YYYY-MM-DD"
    char due[12];
    int step;                // position of the next step, 1-based (= steps when finished)
    int steps;
    char process[64];        // the next step's process (empty when finished)
    char event[16];          // what a scan would record: ARRIVED, STARTED, FINISHED, SENT_OUT, RETURNED
    char box[32];            // "Box 2 of 3" for items with several boxes, else empty
};

class SERVER_CLIENT
{
public:
    // Asks the server which item a tag belongs to and what a scan of it would record.
    tag_info_t lookup(const char *uid);

    // Sends one scan to the factory server and reports what it answered.
    scan_result_t scan(const char *uid);

    // Sends one scan. True if the server answered 2xx.
    bool send_scan(const char *uid);

private:
    // Sends a GET (body == nullptr) or a POST; fills `reply` with the body (NUL-terminated). Returns the HTTP status, or -1.
    int request(const char *path, const char *body, char *reply, size_t reply_size);
};
