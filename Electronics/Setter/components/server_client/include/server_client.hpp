#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_http_client.h"

// One row of GET /api/assignable-items/
struct setter_item_t {
    int id;              // send this back in assign()
    char code[24];       // item code, e.g. "2611-001-01"
    char part[48];       // part name (may contain Japanese UTF-8)
    char customer[48];   // customer name (may contain Japanese UTF-8)
    char order_number[32];   // customer order number, may be empty
    char due[12];        // "YYYY-MM-DD"
    char ordered[12];    // date the order was registered, "YYYY-MM-DD" (empty on an older server)
    int linked;          // boxes already tagged
    int boxes;           // boxes expected
    int quantity;        // pieces
    int steps;           // route steps (processes)
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
    char message[192];   // server "detail" on REFUSED (English or Japanese), otherwise a short English text
    int linked;          // on ASSIGNED: boxes now linked
    int boxes;           // on ASSIGNED: boxes expected
    bool tag_in_use;     // on REFUSED: the tag is linked to another item (current_item)
    char current_item[24];   // on REFUSED + tag_in_use: item code the tag is linked to now
    char released[24];   // on ASSIGNED with replace: item code the tag was moved away from
};

// Where fetch_drawing() puts the picture. It fills `buffer` with `buffer_rows` full rows of `width` pixels
// (RGB565, high byte first, ready for the panel) and calls draw_rows() each time; the last call may have fewer rows.
struct picture_sink_t {
    int width;
    int height;
    uint16_t *buffer;      // DMA-capable, at least width * buffer_rows pixels
    int buffer_rows;
    bool (*draw_rows)(void *ctx, int y, int rows, const uint16_t *pixels);   // false = stop
    void *ctx;
};

// What happened to GET /api/items/<id>/drawing-pixels/
enum class drawing_state_t {
    SHOWN,        // 200: every row arrived and was drawn
    NO_DRAWING,   // 404: the item has no drawing file
    UNREADABLE,   // 422: the server could not open the file
    BAD_KEY,      // 401: wrong key or device inactive
    FAILED,       // no WiFi, timeout, 5xx, picture cut short, or the screen failed
};

class SERVER_CLIENT
{
public:
    // Sends one scan to the factory server. True if the server answered 2xx.
    bool send_scan(const char *uid);

    // Fetches one page of items that still need a tag, in the server's `sort` order ("number", "start", "due", "part").
    // False on any network/HTTP/JSON error.
    bool fetch_items(int offset, int limit, const char *sort, setter_item_t *items, int max_items, int *count, int *total);

    // Links a tag UID to an item id and reports what the server decided.
    // replace = true moves a tag that is linked to another item (only after the operator confirmed).
    assign_result_t assign(const char *uid, int item_id, bool replace = false);

    // Streams the item's drawing, sized sink.width x sink.height by the server, into the sink band by band.
    drawing_state_t fetch_drawing(int item_id, const picture_sink_t &sink);

    // Language of the server's text replies (refusal reasons): English by default
    static void set_language(bool japanese);

private:
    // Sends one request; fills `reply` with the body (NUL-terminated). Returns HTTP status, or -1.
    int request(bool post, const char *path, const char *body, char *reply, size_t reply_size);

    // Sends a request and reads the reply headers. *status = HTTP status or -1.
    // Returns the client to read the body from (always pass it to finish()), or nullptr if nothing was sent.
    esp_http_client_handle_t open(bool post, const char *path, const char *body, int *status);
    void finish(esp_http_client_handle_t client);

    // Reads the 200 body of fetch_drawing() into the sink
    drawing_state_t read_picture(esp_http_client_handle_t client, const picture_sink_t &sink);
};
