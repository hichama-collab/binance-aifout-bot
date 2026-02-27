#!/bin/bash

case "$1" in
  1)
    journalctl -u token-profile-selector.service -n 20 --no-pager
    ;;
  2)
  
    sudo systemctl status binance-aifout-bot.service --no-pager
    ;;
  3)
    sudo systemctl start binance-aifout-bot.service
    ;;
  4)
    sudo systemctl stop binance-aifout-bot.service
    ;;
  *)
    echo "Usage: $0 {1|2|3|4}"
    exit 1
    ;;
esac
