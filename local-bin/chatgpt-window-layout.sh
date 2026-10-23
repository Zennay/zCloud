#!/usr/bin/env bash
set -u
export DISPLAY=:10
export XAUTHORITY=/home/ubuntu/.Xauthority

for _ in $(seq 1 30); do
  mapfile -t IDS < <(xdotool search --onlyvisible --class firefox 2>/dev/null || true)
  [ "${#IDS[@]}" -ge 1 ] && break
  sleep 1
done

for id in $(xdotool search --onlyvisible --name 'Problem loading page' 2>/dev/null || true); do
  xdotool windowclose "$id" || true
done

sleep 1
mapfile -t IDS < <(xdotool search --onlyvisible --class firefox 2>/dev/null || true)
if [ "${#IDS[@]}" -eq 1 ]; then
  xdotool windowmove "${IDS[0]}" 0 0
  xdotool windowsize "${IDS[0]}" 1920 1080
elif [ "${#IDS[@]}" -ge 2 ]; then
  xdotool windowmove "${IDS[0]}" 0 0
  xdotool windowsize "${IDS[0]}" 960 1080
  xdotool windowmove "${IDS[1]}" 960 0
  xdotool windowsize "${IDS[1]}" 960 1080
fi
