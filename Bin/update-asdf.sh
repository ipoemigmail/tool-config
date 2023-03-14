#!/bin/bash

asdf plugin update --all

function update() {
  PLUGIN=$1

  CURRENT_VER=$(asdf current $PLUGIN | awk '{print $2}')
  LATEST_VER=$(asdf latest $PLUGIN)

  RAW_PLUGIN=$(echo ${PLUGIN} | sed -E 's/(.+) .+/\1/g')

  if [[ "${CURRENT_VER}" != "${LATEST_VER}" ]]; then
    asdf install $RAW_PLUGIN ${LATEST_VER}
    asdf global $RAW_PLUGIN ${LATEST_VER}
    asdf reshim $RAW_PLUGIN ${LATEST_VER}
  else
    echo "$PLUGIN already updated (current: $CURRENT_VER, latest: $LATEST_VER)"
  fi
}

update "python" &
update "java zulu-17" &
asdf install java $(asdf latest java zulu-8) &

wait
