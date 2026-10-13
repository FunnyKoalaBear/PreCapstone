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
| Buzzer               | GPIO_NUM_5     | Active-high output              |
| TFT CS               | GPIO_NUM_6     | ST7796S, SPI2                   |
| TFT RESET            | GPIO_NUM_7     |                                  |
| TFT DC               | GPIO_NUM_8     |                                  |
| TFT SDI (MOSI)       | GPIO_NUM_9     | Shared with touch T_DIN         |
| TFT SCK              | GPIO_NUM_12    | Shared with touch T_CLK         |
| TFT SDO (MISO)       | GPIO_NUM_13    | Shared with touch T_DO          |
| TFT LED (backlight)  | tied to 3.3V   | Set `TFT_BACKLIGHT_PIN` if wired to a GPIO instead |
| Touch (XPT2046) CS   | GPIO_NUM_15    | Same SPI bus as TFT, own CS     |
| Touch (XPT2046) IRQ  | GPIO_NUM_16    | Reserved, not used yet (polled instead) |

Board flash: 16MB physical chip; `sdkconfig` currently declares 2MB (`CONFIG_ESPTOOLPY_FLASHSIZE`) with the default `partitions_singleapp.csv` table (no OTA). Fine for now — revisit when WiFi/UI work needs OTA or asset storage.

## Features

### WiFi (station mode)
`components/wifi` — joins a 2.4 GHz network, reconnects on its own, and exposes `connected()` / `wait_connected()` for the HTTP code to come. SSID and password are set under **Scanner WiFi** in `idf.py menuconfig` and live in the gitignored `sdkconfig`, not in source. Confirmed working: the board shows up in the GL.iNet Mango router's DHCP leases. Not sending any data yet.

### TFT display + touch scrollable list (ST7796S SPI, XPT2046 touch)
`components/display` — drives a 4" 480x320 ST7796S over SPI2 (`espressif/esp_lcd_st7796`) with a hand-rolled 8x8 bitmap font (no LVGL yet). No framebuffer fits in RAM (no PSRAM on this board), so every draw streams straight over SPI in 16-row bands. `run_scrollable_list()` reads an XPT2046 touch controller on the same SPI bus (`espressif/esp_lcd_touch` + `atanisoft/esp_lcd_touch_xpt2046`, own CS pin, 1MHz) and lets a finger-drag scroll a vertical list, tracked from the drag's start point rather than frame deltas to avoid drift.
- SPI clock: confirmed working on this board/breadboard at 40MHz (bumped up from an initial conservative 20MHz) — noticeably smoother scrolling, no visible tearing.
- Scrolling redraws the whole screen every tick (no framebuffer to diff against), which caps it well short of phone-smooth. A real fix would use the ST7796S's hardware vertical-scroll registers instead of full redraws, but that interacts awkwardly with the `SWAP_XY` orientation flag and hasn't been attempted.
- Touch `mirror_x`/`mirror_y` are both off — flip in `DISPLAY::init_touch()` if a drag ever moves the wrong way.

### Buzzer
`components/buzzer` — active-high GPIO output (`on()`/`off()`), driven from `button_task` so it beeps while the steel button is held. No dedicated task; it's just reactive output tied to the button's own poll loop.

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
- Real UI content (the scrollable list currently shows 20 placeholder items — swap in real content, e.g. scan history)

## How this file gets updated

Add a new entry under **Features** (or **Planned**) each time a feature is completed, with a short "what it does" + any non-obvious wiring/config decision. Keep entries terse — this is a build log, not a spec.
