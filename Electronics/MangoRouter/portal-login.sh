#!/bin/sh
# Logs the router in to the campus captive portal.
# Installed as /root/portal-login.sh; run by /root/wan-watchdog.sh when the
# campus WiFi is connected but the internet check fails.
# It reads the portal's own login form (action URL, hidden fields, user and
# password field names); if that fails it posts to the known KUAS endpoint.
# KUAS form (checked 2026-10-27): POST /auth/index.html/u with user, password.
# Everything it saw is kept in /tmp/portal/ (no password stored there).

LOG="logger -t portal-login"
PROBE=http://detectportal.firefox.com/success.txt
D=/tmp/portal
mkdir -p $D

. /root/.portal_creds 2>/dev/null
[ -n "$PORTAL_USER" ] && [ -n "$PORTAL_PASS" ] || { $LOG "missing /root/.portal_creds"; exit 1; }
command -v curl >/dev/null || { $LOG "curl not installed: opkg update && opkg install curl"; exit 1; }

# -k: the router has no CA bundle, so the portal's certificate is not verified
C="curl -sk -m 15 -A Mozilla/5.0 -c $D/cookies -b $D/cookies"

online() { [ "$($C $PROBE 2>/dev/null)" = "success" ]; }
online && exit 0

# 1. Load the portal page (follow redirects, keep the final URL)
url=$($C -L -o $D/page.html -w '%{url_effective}' "$PROBE")
if ! grep -qi '<form' $D/page.html; then
  # Portal sent a meta-refresh / JavaScript redirect instead of a form: follow it once
  next=$(grep -oiE "(url=|location(\.href)? *= *[\"'])[^\"' >;]+" $D/page.html | head -n1 | sed -E "s/^[^=]*= *[\"']?//")
  case "$next" in
    "") ;;
    http*) ;;
    /*) next="$(echo "$url" | grep -oE '^https?://[^/]+')$next" ;;
    *) next="${url%%\?*}"; next="${next%/*}/$next" ;;
  esac
  [ -n "$next" ] && url=$($C -L -o $D/page.html -w '%{url_effective}' "$next")
fi
echo "$url" > $D/url

origin=$(echo "$url" | grep -oE '^https?://[^/]+')
base="${url%%\?*}"

submit() {  # $1 = action URL, $2 = user field, $3 = password field, rest = extra curl args
  a=$1; uf=$2; pf=$3; shift 3
  $C -L -o $D/result.html -e "$url" "$@" \
     --data-urlencode "$uf=$PORTAL_USER" --data-urlencode "$pf=$PORTAL_PASS" "$a"
  sleep 3
  online
}

# 2. Read the login form on the portal page
form=$(tr '\n\r' '  ' < $D/page.html | grep -oiE '<form[^>]*>.*</form>' | head -n1)
action=$(echo "$form" | grep -oiE "<form[^>]*" | grep -oiE "action=[\"'][^\"']*" | head -n1 | cut -c9- | sed 's/&amp;/\&/g')
case "$action" in
  "") action="$url" ;;
  http*) ;;
  /*) action="$origin$action" ;;
  *) action="${base%/*}/$action" ;;
esac

echo "$form" | grep -oiE '<input[^>]*>' > $D/inputs
set --
userf=""; passf=""
while read -r tag; do
  name=$(echo "$tag" | grep -oiE "[[:space:]]name=[\"'][^\"']*" | head -n1 | sed -E "s/^[[:space:]][^=]*=[\"']//")
  [ -n "$name" ] || continue
  type=$(echo "$tag" | grep -oiE "type=[\"'][^\"']*" | head -n1 | cut -c7- | tr 'A-Z' 'a-z')
  value=$(echo "$tag" | grep -oiE "value=[\"'][^\"']*" | head -n1 | cut -c8-)
  case "$type" in
    password) [ -z "$passf" ] && passf="$name"; continue ;;
    text|email|"") if [ -z "$userf" ] && [ -z "$value" ]; then userf="$name"; continue; fi ;;
    checkbox) [ -n "$value" ] || value=on ;;
    radio) continue ;;
    submit|button|image|reset) [ -n "$value" ] || continue ;;
  esac
  set -- "$@" --data-urlencode "$name=$value"
done < $D/inputs

# 3a. Submit the form exactly as the page defines it
if [ -n "$form" ] && [ -n "$userf" ] && [ -n "$passf" ]; then
  echo "form: action=$action user_field=$userf pass_field=$passf other_fields=$(( $# / 2 ))" > $D/form
  if submit "$action" "$userf" "$passf" "$@"; then
    $LOG "login OK via page form ($action)"; exit 0
  fi
  $LOG "page form login failed, trying known KUAS endpoint"
else
  echo "form: none usable on $url" > $D/form
fi

# 3b. Known KUAS Aruba endpoint (used when the page could not be read)
[ -n "$origin" ] || origin=https://uzwlan03.kuas.ac.jp
known="$origin/auth/index.html/u"
if [ "$known" != "$action" ] || [ -z "$form" ]; then
  echo "known: $known" >> $D/form
  if submit "$known" user password; then
    $LOG "login OK via known endpoint ($known)"; exit 0
  fi
fi
$LOG "login failed (see $D/)"; exit 1
