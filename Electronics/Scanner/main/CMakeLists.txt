idf_component_register(SRCS "main.cpp"
                    
                    INCLUDE_DIRS "." 
                    
                    REQUIRES led_strip
                    rfid
                    button
                    buzzer
                    scanner_display
                    wifi
                    button_panel
                    keyboard
                    scanner_ui
                    esp_netif
                    )
