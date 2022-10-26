#!/bin/bash

asdf plugin update --all

asdf install rust latest && asdf global rust latest
asdf install golang latest && asdf global golang latest
asdf install kotlin latest && asdf global kotlin latest
asdf install zig latest && asdf global zig latest
asdf install python latest && asdf global python latest

asdf install java $(asdf latest java zulu-17) && asdf global java $(asdf latest java zulu-17)
asdf install java $(asdf latest java zulu-8)
