#pragma once

#include <stddef.h>

// What the server decided about one scan (see Web_Application/docs/scanner-api.md)
enum class scan_state_t {
    RECORDED,       // 200 recorded: an event was saved
    FINISHED,       // 200 finished: route already complete, nothing saved
    DUPLICATE,      // 200 duplicate: same tag scanned again too soon, nothing saved
    UNASSIGNED,     // 404: tag not linked to an item
    BAD_KEY,        // 401: wrong key or device inactive
    INVALID,        // 400 / 405: firmware sent bad data
    SERVER_ERROR,   // 5xx, timeout, no WiFi: worth scanning again
};

struct scan_result_t {
    scan_state_t state;
    char uid[24];          // tag UID as hex, e.g. "0D11696B"
    char item[24];         // item code, e.g. "2611-001-01" (empty when the server didn't say)
    char process[48];      // process just recorded (may contain Japanese UTF-8)
    char event_type[16];   // STARTED, FINISHED, ARRIVED, SENT_OUT or RETURNED
    char status[32];       // item status after the scan, e.g. "In progress"
};

// What GET /api/scan-preview/ said: the order and the step a scan would record, nothing saved
enum class preview_state_t {
    NEXT,           // 200 next: a scan would record `process` / `event_type`
    FINISHED,       // 200 finished: route complete, a scan would save nothing
    UNASSIGNED,     // 404 unassigned_tag
    BAD_KEY,        // 401
    INVALID,        // 400, or a server without the preview endpoint
    SERVER_ERROR,   // 5xx, timeout, no WiFi
};

struct scan_preview_t {
    preview_state_t state;
    char uid[24];
    char item[24];            // "2611-001-01"
    char part[64];            // part name (may contain Japanese UTF-8)
    char drawing[32];         // drawing number, may be empty
    char revision[12];
    char material[48];
    int quantity;
    char customer[64];
    char order_number[48];    // customer order number, may be empty
    char ordered[12];         // "YYYY-MM-DD" the order was registered
    char due[12];             // "YYYY-MM-DD"
    char status[32];          // item status now, e.g. "In progress"
    int step;                 // position of the step in the route (1-based)
    int steps;                // steps in the route
    char process[48];         // process a scan would record
    char event_type[16];      // event a scan would record
};

class SERVER_CLIENT
{
public:
    // Sends one scan to the factory server and reports what it decided.
    scan_result_t send_scan(const char *uid);

    // Asks what a scan of this tag would record, without saving anything.
    scan_preview_t preview(const char *uid);

private:
    // Sends one request; fills `reply` with the reply body (NUL-terminated). Returns HTTP status, or -1.
    int request(bool post, const char *path, const char *body, char *reply, size_t reply_size);
};
