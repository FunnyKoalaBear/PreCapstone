# Router uplink watchdog (GL-MT300N-V2, OpenWrt 19.07)

The router runs as a single-radio repeater: it joins the campus WiFi (`kuas-wlan`) as a client
and serves the project LAN (`192.168.8.x`) on the same radio. When the campus link drops, the
router's own network can go down with it and not come back without a power cycle.
This watchdog reboots the router automatically instead.

## What it does

- Checks every 10 s whether the uplink (`wwan` on `wlan0`, via `ifstatus`) is up.
  (`iwinfo` can't be used: on this driver it shows the router's own MAC as the access point.)
- Writes state changes (uplink down/back, no internet/internet back) to `/root/net-events.log`,
  which survives reboots (`logread` only keeps the last ~16 h and is lost on reboot).
- If the uplink has been down for 30 s (and the router has been up for over 3 min),
  saves the last 150 log lines to `/root/last-drop.log` and reboots.
- Every 60 s (and ~10 s after boot), if the uplink is up but there is no internet,
  runs `/root/portal-login.sh` when that script exists (captive portal login, see below).
- Stuck link: if login doesn't help and the campus gateway doesn't answer pings, the link is
  "up" but dead (seen 2026-11-05: connected for hours, 100% ping loss, fixed only by a power
  cycle). After 2 min it reconnects the WiFi (`wifi`, clients drop ~10 s), every 3 min after
  that it tries again, and after 5 min it reboots if the router has been up over 30 min.
- Also sets the radio to 20 MHz channel width (`HT20`) to reduce interference.

## Files

| Repo file | On the router |
|---|---|
| `wan-watchdog.sh` | `/root/wan-watchdog.sh` |
| `wan-watchdog.init` | `/etc/init.d/wan-watchdog` |
| `portal_creds.example` | template for `/root/.portal_creds` (chmod 600, never committed) |
| `install-watchdog.sh` | installer that writes both files, enables the service, sets HT20 |
| `portal-login.sh` | `/root/portal-login.sh` (needs `curl`) |

## Install (Windows PowerShell)

The router's old Dropbear SSH (2019.78) only offers `ssh-rsa`, so it must be allowed explicitly.
It also rejects ed25519 keys; the laptop logs in with the RSA key `~/.ssh/mango_rsa`
(`ssh -i ~/.ssh/mango_rsa ...`), listed in `/etc/dropbear/authorized_keys`.

```powershell
Get-Content -Raw .\install-watchdog.sh | ssh -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa root@192.168.8.1 "tr -d '\r' > /tmp/i.sh && sh /tmp/i.sh"
```

Update only the watchdog script later (no WiFi reload):

```powershell
Get-Content -Raw .\wan-watchdog.sh | ssh -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa root@192.168.8.1 "tr -d '\r' > /root/wan-watchdog.sh && chmod +x /root/wan-watchdog.sh && /etc/init.d/wan-watchdog restart"
```

Portal credentials (replace the placeholders; clear PowerShell history afterwards):

```powershell
"PORTAL_USER=YOUR_ID`nPORTAL_PASS=YOUR_PASSWORD`n" | ssh -o HostKeyAlgorithms=+ssh-rsa root@192.168.8.1 "tr -d '\r' > /root/.portal_creds && chmod 600 /root/.portal_creds && echo saved"
```

## Portal auto-login

KUAS uses an Aruba captive portal. Its login page posts `user` and `password` to
`https://uzwlan03.kuas.ac.jp/auth/index.html/u`; the script reads that form from the page and
falls back to this endpoint if it can't. Login is tied to the router's WAN MAC, so every LAN device
gets internet once the router is logged in.

Install while the router is online (so `opkg` can fetch `curl`):

```powershell
Get-Content -Raw .\portal-login.sh | ssh -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa root@192.168.8.1 "tr -d '\r' > /root/portal-login.sh && chmod +x /root/portal-login.sh && (command -v curl || (opkg update && opkg install curl))"
```

Test (logs the router out, then back in):

```sh
curl -sk -o /dev/null 'https://uzwlan03.kuas.ac.jp/auth/logout.html'; sleep 3
/root/portal-login.sh; logread | grep portal-login | tail -n 3; cat /tmp/portal/form
```

If it fails, `/tmp/portal/` holds the page, the URL and the portal's reply (no password).

## Operate

```sh
ps | grep [w]an-watchdog                 # is it running?
logread | grep wan-watchdog              # what it has done since boot
cat /root/net-events.log                 # when uplink / internet went down and came back
cat /root/last-drop.log                  # log saved just before the last auto-reboot
/etc/init.d/wan-watchdog stop; /etc/init.d/wan-watchdog disable   # turn it off
```

## Notes

- The watchdog will reboot every few minutes while the campus WiFi is unavailable.
- Long term (factory deployment): give the router a wired uplink or none at all; the
  ESP32 -> FastAPI traffic is LAN-only and doesn't need the internet.
