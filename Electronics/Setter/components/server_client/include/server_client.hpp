#pragma once

#include <stddef.h>

// One row of GET /api/assignable-items/ (only the ASCII-safe fields the screen needs)
struct setter_item_t {
    int id;              // send this back in assign()
    char code[24];       // item code, e.g. "2611-001-01"
    char part[48];       // part name (may contain Japanese UTF-8)
    char due[12];        // "YYYY-MM-DD"
    int linked;          // boxes already tagged
    int boxes;           // boxes expected
};

// What happened to POST /api/assign/
enum class assign_state_t {
    ASSIGNED,       // 200: linked
    REFUSED,        // 409: tag in use/inactive or item full; message has the reason
    BAD_KEY,        // 401: wrong key or device inactive
    INVALID,        // 400: firmware sent bad data
    SERVER_ERROR,   // 5xx, timeout, no WiFi: worth a retry
};

struct assign_result_t {
    assign_state_t state;
    char message[128];   // server "detail" on REFUSED, otherwise a short English text
    int linked;          // on ASSIGNED: boxes now linked
    int boxes;           // on ASSIGNED: boxes expected
};

class SERVER_CLIENT
{
public:
    // Sends one scan to the factory server. True if the server answered 2xx.
    bool send_scan(const char *uid);

    // Fetches one page of items that still need a tag. False on any network/HTTP/JSON error.
    bool fetch_items(int offset, int limit, setter_item_t *items, int max_items, int *count, int *total);

    // Links a tag UID to an item id and reports what the server decided.
    assign_result_t assign(const char *uid, int item_id);

private:
    // Sends one request; fills `reply` with the body (NUL-terminated). Returns HTTP status, or -1.
    int request(bool post, const char *path, const char *body, char *reply, size_t reply_size);
};
