idf_component_register(SRCS "main.cpp"
                    
                    INCLUDE_DIRS "." 
                    
                    REQUIRES led_strip
                    rfid
                    button
                    buzzer
                    scanner_display
                    wifi
                    server_client
                    )
