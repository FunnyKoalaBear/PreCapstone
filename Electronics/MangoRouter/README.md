# Router uplink watchdog (GL-MT300N-V2, OpenWrt 19.07)

The router runs as a single-radio repeater: it joins the campus WiFi (`kuas-wlan`) as a client
and serves the project LAN (`192.168.8.x`) on the same radio. When the campus link drops, the
router's own network can go down with it and not come back without a power cycle.
This watchdog reboots the router automatically instead.

## What it does

- Checks every 10 s whether the uplink (`wlan0`) is associated with `kuas-wlan`.
- If the uplink has been down for 30 s (and the router has been up for over 3 min),
  saves the last 150 log lines to `/root/last-drop.log` and reboots.
- Every 60 s (and ~10 s after boot), if the uplink is up but there is no internet,
  runs `/root/portal-login.sh` when that script exists (captive portal login; not written yet).
- Also sets the radio to 20 MHz channel width (`HT20`) to reduce interference.

## Files

| Repo file | On the router |
|---|---|
| `wan-watchdog.sh` | `/root/wan-watchdog.sh` |
| `wan-watchdog.init` | `/etc/init.d/wan-watchdog` |
| `portal_creds.example` | template for `/root/.portal_creds` (chmod 600, never committed) |
| `install-watchdog.sh` | installer that writes both files, enables the service, sets HT20 |

## Install (Windows PowerShell)

The router's old Dropbear SSH only offers `ssh-rsa`, so it must be allowed explicitly.

```powershell
Get-Content -Raw .\install-watchdog.sh | ssh -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa root@192.168.8.1 "tr -d '\r' > /tmp/i.sh && sh /tmp/i.sh"
```

Portal credentials (replace the placeholders; clear PowerShell history afterwards):

```powershell
"PORTAL_USER=YOUR_ID`nPORTAL_PASS=YOUR_PASSWORD`n" | ssh -o HostKeyAlgorithms=+ssh-rsa root@192.168.8.1 "tr -d '\r' > /root/.portal_creds && chmod 600 /root/.portal_creds && echo saved"
```

## Operate

```sh
ps | grep [w]an-watchdog                 # is it running?
logread | grep wan-watchdog              # what it has done since boot
cat /root/last-drop.log                  # log saved just before the last auto-reboot
/etc/init.d/wan-watchdog stop; /etc/init.d/wan-watchdog disable   # turn it off
```

## Notes

- The watchdog will reboot every few minutes while the campus WiFi is unavailable.
- Long term (factory deployment): give the router a wired uplink or none at all; the
  ESP32 -> FastAPI traffic is LAN-only and doesn't need the internet.
