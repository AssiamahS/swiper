#!/bin/sh
# deploy the swiper-judge worker with the current swiper.js + signed Shortcut as its static files
set -e
cd "$(dirname "$0")"
mkdir -p public
cp ../swiper.js public/swiper.js
[ -f ../shortcut/Swiper.shortcut ] && cp ../shortcut/Swiper.shortcut public/Swiper.shortcut
wrangler deploy
