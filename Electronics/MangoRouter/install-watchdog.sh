#!/bin/sh
# Installs the uplink watchdog on the GL-MT300N-V2. Safe to run more than once.
set -e

cat > /root/wan-watchdog.sh <<'EOF'
#!/bin/sh
# Checks the campus WiFi uplink every 10 s; reboots if it has been down 30 s.
STA=wlan0; LOG="logger -t wan-watchdog"; down=0; tick=0
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
EOF
chmod +x /root/wan-watchdog.sh

cat > /etc/init.d/wan-watchdog <<'EOF'
#!/bin/sh /etc/rc.common
START=99
USE_PROCD=1
start_service() {
  procd_open_instance
  procd_set_param command /root/wan-watchdog.sh
  procd_set_param respawn
  procd_close_instance
}
EOF
chmod +x /etc/init.d/wan-watchdog
/etc/init.d/wan-watchdog enable
/etc/init.d/wan-watchdog restart

uci set wireless.radio0.htmode='HT20'
uci commit wireless

echo "Watchdog installed and running. WiFi will reload in 3 s (devices drop briefly)."
( sleep 3; wifi reload ) >/dev/null 2>&1 &
