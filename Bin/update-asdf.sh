#!/bin/bash

asdf plugin update --all

asdf install rust latest
asdf install golang latest
asdf install kotlin latest
asdf install zig latest
asdf install python latest

asdf install java $(asdf latest java zulu-17)
asdf install java $(asdf latest java zulu-8)
