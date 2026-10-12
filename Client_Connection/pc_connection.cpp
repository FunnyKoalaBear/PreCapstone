#include <httplib.h>
#include <iostream>
#include <string>

int main() {

    httplib::Client client("http://127.0.0.1:8000");
    client.set_connection_timeout(3);
    client.set_read_timeout(5);

    std::string body = R"({"uid": "From C++"})";
    auto res = client.Post("/record_scan", body, "application/json");   
  
    if (!res) {
        std::cout << "request failed: " << 
        httplib::to_string(res.error()) << "\n";

        return 1; 
    }

    std::cout << "status: " << res->status << "\n";
    std::cout << "body:   " << res->body << "\n";

}

// cd C:\Users\sbmit\OneDrive\Codine\PreCapstone\Client_Connection
// cmake --build build --config Release
// .\build\Release\scan_client.exe