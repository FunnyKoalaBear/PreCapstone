#!/bin/sh
# Checks the campus WiFi uplink every 10 s; reboots if it has been down 30 s.
STA=wlan0; LOG="logger -t wan-watchdog"; down=0; tick=5
associated() { iwinfo $STA info 2>/dev/null | grep 'Access Point:' | grep -qv '00:00:00:00:00:00'; }
while true; do
  if associated; then
    down=0; tick=$((tick+1))
    if [ $((tick % 6)) -eq 0 ] && [ -x /root/portal-login.sh ] &&
       [ "$(wget -q -T 5 -O - http://detectportal.firefox.com/success.txt 2>/dev/null)" != "success" ]; then
      $LOG "no internet: portal login"; /root/portal-login.sh
    fi
  else
    down=$((down+10)); $LOG "uplink down ${down}s"
    if [ $down -ge 30 ] && [ $(cut -d. -f1 /proc/uptime) -gt 180 ]; then
      $LOG "uplink down 30s: rebooting"
      logread | tail -n 150 > /root/last-drop.log; sync; reboot; exit
    fi
  fi
  sleep 10
done
