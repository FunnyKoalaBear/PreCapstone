idf_component_register(SRCS "main.cpp"
                    
                    INCLUDE_DIRS "." 
                    
                    REQUIRES led_strip
                    rfid
                    button
                    buzzer
                    setter_display
                    wifi
                    server_client
                    button_panel
                    )
