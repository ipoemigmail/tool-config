#!/bin/zsh

source ~/.zshrc
brew update; brew upgrade --greedy -y; brew upgrade --cask --greedy -y; rustup update stable --force; mise upgrade; cargo install-update -a; omz update; pi update
