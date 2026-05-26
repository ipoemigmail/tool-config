#!/bin/bash

cd $HOME/Develop/Personal/my-proxy
RUST_LOG=my_proxy=trace cargo run --release -- 0.0.0.0:3131 5 > /dev/null 2>&1 &

