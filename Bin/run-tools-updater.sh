#!/bin/zsh

source ~/.zshrc
brew update; brew upgrade --greedy; brew upgrade --cask --greedy; rustup update stable --force; mise upgrade; cargo install-update -a; omz update
