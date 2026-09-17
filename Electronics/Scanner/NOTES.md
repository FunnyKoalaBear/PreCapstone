# Scanner — Build Notes

Running log of what's built, how it's wired, and why. Updated as each feature lands — newest entries at the top.

## Hardware / pin reference

| Signal              | GPIO           | Notes                          |
|---------------------|----------------|---------------------------------|
| Steel button         | GPIO_NUM_4     | Active-low, internal pull-up   |
| RFID (PN532) SCL     | GPIO_NUM_10    | I2C                             |
| RFID (PN532) SDA     | GPIO_NUM_11    | I2C                             |
| RFID (PN532) RESET   | GPIO_NUM_NC    | Not connected                   |
| RFID (PN532) IRQ     | GPIO_NUM_NC    | Not connected                   |

Board flash: 16MB physical chip; `sdkconfig` currently declares 2MB (`CONFIG_ESPTOOLPY_FLASHSIZE`) with the default `partitions_singleapp.csv` table (no OTA). Fine for now — revisit when WiFi/UI work needs OTA or asset storage.

## Features

### Centralized pin config
`components/configuration/include/configuration.hpp` holds all GPIO pin assignments as `constexpr` in a `config` namespace, so `button` and `rfid` components don't hardcode pins.

### RFID scanning (PN532, I2C)
`components/rfid` — initializes a PN532 reader over I2C, polls for ISO14443A cards, and reads NTAG213/215/216 pages once a card is found. Runs as its own FreeRTOS task (`rfid_task` in `main/main.cpp`), polling every 100ms.

### Steel button input
`components/button` — active-low digital input on a debounce-free poll loop. Runs as its own FreeRTOS task (`button_task`), polling every 50ms.

### Dual functionality (button + RFID concurrently)
Both button and RFID run as separate FreeRTOS tasks so scanning and button presses are handled independently rather than blocking each other in a single loop.

## Planned / not started

- WiFi functionality
- UI

## How this file gets updated

Add a new entry under **Features** (or **Planned**) each time a feature is completed, with a short "what it does" + any non-obvious wiring/config decision. Keep entries terse — this is a build log, not a spec.
