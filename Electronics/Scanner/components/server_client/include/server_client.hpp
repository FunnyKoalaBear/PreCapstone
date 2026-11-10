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

class SERVER_CLIENT
{
public:
    // Sends one scan to the factory server and reports what it answered.
    scan_result_t scan(const char *uid);

    // Sends one scan. True if the server answered 2xx.
    bool send_scan(const char *uid);

private:
    // Sends a POST; fills `reply` with the body (NUL-terminated). Returns the HTTP status, or -1.
    int post_json(const char *path, const char *body, char *reply, size_t reply_size);
};
