#!/bin/sh
# Checks the campus WiFi uplink every 10 s; reconnects or reboots when it is down or stuck.
LOG="logger -t wan-watchdog"; EV=/root/net-events.log; PROBE=http://detectportal.firefox.com/success.txt
down=0; tick=5; net=1; stuck=0
# netifd marks wwan down as soon as the WiFi link drops (iwinfo shows our own MAC, so it can't be used)
uplink() { ifstatus wwan 2>/dev/null | grep -q '"up": true'; }
online() { [ "$(wget -q -T 5 -O - $PROBE 2>/dev/null)" = "success" ]; }
# A stuck link stays "up" but the campus gateway stops answering pings
gateway_ok() { gw=$(ip route | awk '/^default/ {print $3; exit}'); [ -n "$gw" ] && ping -c 3 -W 2 $gw >/dev/null 2>&1; }
uptime_s() { cut -d. -f1 /proc/uptime; }
# Logs to logread and to a small file in flash that survives reboots
event() { $LOG "$1"; echo "$(date '+%F %T') $1" >> $EV; tail -n 200 $EV > $EV.tmp && mv $EV.tmp $EV; }
reboot_now() { event "$1: rebooting"; logread | tail -n 150 > /root/last-drop.log; sync; reboot; exit; }
while true; do
  if uplink; then
    [ $down -gt 0 ] && event "uplink back after ~${down}s"
    down=0; tick=$((tick+1))
    if [ $((tick % 6)) -eq 0 ]; then
      if online; then
        [ $net -eq 0 ] && event "internet back"; net=1; stuck=0
      else
        [ $net -eq 1 ] && event "no internet"; net=0
        if [ -x /root/portal-login.sh ] && /root/portal-login.sh; then
          event "internet back (portal login)"; net=1; stuck=0
        elif gateway_ok; then
          stuck=0
        else
          stuck=$((stuck+1)); event "gateway not answering (${stuck} min)"
          if [ $stuck -ge 5 ] && [ $(uptime_s) -gt 1800 ]; then
            reboot_now "link stuck ${stuck} min"
          elif [ $((stuck % 3)) -eq 2 ]; then
            event "link stuck: reconnecting WiFi"; wifi; sleep 20
          fi
        fi
      fi
    fi
  else
    down=$((down+10)); $LOG "uplink down ${down}s"
    [ $down -eq 10 ] && event "uplink down"
    [ $down -ge 30 ] && [ $(uptime_s) -gt 180 ] && reboot_now "uplink down 30s"
  fi
  sleep 10
done
