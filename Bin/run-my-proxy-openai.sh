#!/bin/bash

cd $HOME/Develop/Personal/my-proxy
RUST_LOG=my_proxy=trace cargo run --release -- --target https://api.openai.com --header-timeout 5 --bind 0.0.0.0:3000
#> /dev/null 2>&1 &

