#!/bin/bash

asdf plugin update --all

function update() {
  PLUGIN=$1

  CURRENT_VER=$(asdf current $PLUGIN | awk '{print $2}')
  LATEST_VER=$(asdf latest $PLUGIN)

  if [[ "${CURRENT_VER}" != "${LATEST_VER}" ]]; then
    asdf install $PLUGIN latest
    asdf global $PLUGIN latest
  else
    echo "$PLUGIN already updated (current: $CURRENT_VER, latest: $LATEST_VER)"
  fi
}

update "rust" &
update "golang" &
update "kotlin" &
update "zig" &
update "python" &
update "java zulu-17" &
update "nodejs" &
asdf install java $(asdf latest java zulu-8) &

wait
