# Tailscale cheat sheet (factory server)

Tailscale = private network between your own devices (laptop, phone, factory-server), from anywhere, no open router ports.
Funnel = Tailscale feature that makes one local port public on the internet over HTTPS.

| | Address | Who can open it |
|---|---|---|
| Dashboard, public | `https://factory-server.tail8b0d7e.ts.net/` | Anyone (Funnel) |
| Dashboard, private | `http://factory-server:8080/` | Devices logged into the FunnyKoalaBear tailnet |
| SSH | `ssh precap@factory-server` | Tailnet devices |
| Admin console | https://login.tailscale.com/admin/machines | You |

## It starts by itself
- `tailscaled` is a systemd service enabled at boot, and remembers it was logged in and that Funnel is on.
- No network at boot is fine: it keeps retrying and reconnects as soon as Wi-Fi or the cable is up. The web apps listen on all interfaces, so they are reachable the moment the network appears.
- `--operator=precap` was set at install, so the commands below need no sudo (except where shown).

## Everyday commands
```bash
tailscale status                      # am I connected? which devices are online?
tailscale ip -4                       # this machine's Tailscale IP (100.x.y.z)
tailscale funnel status               # is the public URL on?
tailscale netcheck                    # connection quality / nearest relay
```

## Start / stop
```bash
tailscale up                          # connect (after "down", or if it shows "Stopped")
tailscale down                        # disconnect; stays off until "tailscale up"
sudo systemctl restart tailscaled     # restart the background service if it misbehaves
```

## Public URL (Funnel) on / off
```bash
tailscale funnel --bg http://127.0.0.1:8081   # on: public HTTPS -> Caddy's Funnel entrance -> Django
tailscale funnel --https=443 off              # off: LAN and private tailnet access keep working
```
Port 8081 is the localhost-only Caddy entrance that tells Django the visitor used HTTPS (see `/etc/caddy/Caddyfile`). Don't point Funnel at 8080, or logins fail the CSRF check.

## Logged out / new machine
```bash
sudo tailscale up --operator=precap --hostname=factory-server
```
Open the printed `https://login.tailscale.com/a/...` link and approve. If the terminal prompt misbehaves, run it with `< /dev/null`.
Fresh install: `sudo bash server_setup/install-tailscale.sh`, then `sudo bash server_setup/setup-funnel.sh` (see `SERVER_SETUP.md`).

## From the laptop
- Install Tailscale, log in with the same account, then use `factory-server` as the hostname anywhere (SSH, browser, VS Code Remote-SSH).
- Postgres stays local-only on purpose. To reach it with pgAdmin from the laptop, tunnel through SSH:
  `ssh -L 6900:localhost:6900 precap@factory-server`, then connect pgAdmin to `localhost:6900`.

## Monitoring traffic
```bash
tailscale status                              # which of your devices are online / connected right now
tailscale status --json | grep -E '"HostName"|"RxBytes"|"TxBytes"'   # bytes per device
ip -s -h link show tailscale0                 # total bytes in/out over Tailscale since boot
sudo apt install nload && nload tailscale0    # live bandwidth graph (q to quit)
```
- Funnel visitors show up only as `funnel-ingress-node` peers (Tailscale's relays), not as individual people.
- Per-visitor logs (who opened which page, from which IP) come from Caddy's access log, not from Tailscale. It is off by default; enabling it means adding a `log` block to the `:8081` site in `/etc/caddy/Caddyfile`.
- Admin console > Machines shows each device's last seen time. Tailscale's network flow logs are a paid-plan feature.

## Troubleshooting
| Symptom | Check |
|---|---|
| Public URL doesn't load | `tailscale funnel status`; `systemctl is-active caddy dashboard`; wait a minute after network changes |
| "Bad Request (400)" | the hostname is missing from `ALLOWED_HOSTS` in `Web_Application/.env`; restart `dashboard` after editing |
| Login fails with 403 CSRF | Funnel points at 8080 instead of 8081 |
| `factory-server` name not found from laptop | laptop not logged into Tailscale, or MagicDNS off in the admin console |
