#!/bin/bash

export CROSSOVER_HOME="/Applications/CrossOver.app/Contents/SharedSupport/CrossOver"
export WINE="$CROSSOVER_HOME/bin/wineloader"
export WINEPREFIX="$HOME/Library/Application Support/CrossOver/Bottles/Steam-2"
export LC_ALL=ko_KR.UTF-8
export DYLD_LIBRARY_PATH="$CROSSOVER_HOME/lib:$CROSSOVER_HOME/lib64:$CROSSOVER_HOME/lib64/apple_gptk/external"
WINEMSYNC=1 \
  WINEESYNC=1 \
  WINEDEBUG="-all" \
  DYLD_FALLBACK_LIBRARY_PATH="$DYLD_LIBRARY_PATH" \
  WINEDLLPATH="$CROSSOVER_HOME/lib/wine/x86_64-unix" \
  WINEDLLPATH_PREPEND="$CROSSOVER_HOME/lib64/apple_gptk/wine" \
  CX_APPLEGPTK_LIBD3DSHARED_PATH="$CROSSOVER_HOME/lib64/apple_gptk/external/libd3dshared.dylib" \
  WINE_GST_PLUGIN_SYSTEM_PATH_64="$CROSSOVER_HOME/lib64/gstreamer-1.0" \
  "$WINE" "$WINEPREFIX/drive_c/Program Files (x86)/Steam/steam.exe"
