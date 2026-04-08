#!/bin/bash
while true; do
  curl -s -o /dev/null --max-time 10 https://dungeon-clicker-wne7.onrender.com/api/me
  sleep 300
done
