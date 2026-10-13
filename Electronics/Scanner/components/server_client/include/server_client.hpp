#pragma once

class SERVER_CLIENT
{
public:
    // Sends one scan to the factory server. True if the server answered 2xx.
    bool send_scan(const char *uid);

private:
    bool post_json(const char *path, const char *body);
};
