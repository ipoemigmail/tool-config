if [ -f ~/.profile_default ]; then
  source ~/.profile_default
fi

. "$HOME/.cargo/env"

export PATH="${PATH}:${HOME}/.krew/bin"
#export CLAUDE_CODE_MAX_OUTPUT_TOKENS=32000
export K9S_FEATURE_GATE_NODE_SHELL=true
ulimit -n 4096

if [ "$(arch)" != "arm64" ]; then
    export PATH="/usr/local/bin:/usr/local/sbin:$PATH"
else
    export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
fi

# zerobrew
export ZEROBREW_DIR=/Users/ipoemi/.zerobrew
export ZEROBREW_BIN=/Users/ipoemi/.zerobrew/bin
export ZEROBREW_ROOT=/opt/zerobrew
export ZEROBREW_PREFIX=/opt/zerobrew/prefix
export PKG_CONFIG_PATH="$ZEROBREW_PREFIX/lib/pkgconfig:${PKG_CONFIG_PATH:-}"

# SSL/TLS certificates (only if ca-certificates is installed)
if [ -f "$ZEROBREW_PREFIX/opt/ca-certificates/share/ca-certificates/cacert.pem" ]; then
  export CURL_CA_BUNDLE="$ZEROBREW_PREFIX/opt/ca-certificates/share/ca-certificates/cacert.pem"
  export SSL_CERT_FILE="$ZEROBREW_PREFIX/opt/ca-certificates/share/ca-certificates/cacert.pem"
elif [ -f "$ZEROBREW_PREFIX/etc/ca-certificates/cacert.pem" ]; then
  export CURL_CA_BUNDLE="$ZEROBREW_PREFIX/etc/ca-certificates/cacert.pem"
  export SSL_CERT_FILE="$ZEROBREW_PREFIX/etc/ca-certificates/cacert.pem"
elif [ -f "$ZEROBREW_PREFIX/share/ca-certificates/cacert.pem" ]; then
  export CURL_CA_BUNDLE="$ZEROBREW_PREFIX/share/ca-certificates/cacert.pem"
  export SSL_CERT_FILE="$ZEROBREW_PREFIX/share/ca-certificates/cacert.pem"
fi

if [ -d "$ZEROBREW_PREFIX/etc/ca-certificates" ]; then
  export SSL_CERT_DIR="$ZEROBREW_PREFIX/etc/ca-certificates"
elif [ -d "$ZEROBREW_PREFIX/share/ca-certificates" ]; then
  export SSL_CERT_DIR="$ZEROBREW_PREFIX/share/ca-certificates"
fi

# Helper function to safely append to PATH
_zb_path_append() {
    local argpath="$1"
    case ":${PATH}:" in
        *:"$argpath":*) ;;
        *) export PATH="$argpath:$PATH" ;;
    esac;
}

_zb_path_append "$ZEROBREW_BIN"
_zb_path_append "$ZEROBREW_PREFIX/bin"

export PATH="$HOME/.local/bin:/Users/ben.jeong1/.local/share/mise/shims:$PATH"
export MISE_SHELL=zsh
export __MISE_ORIG_PATH="$PATH"

mise() {
  local command
  command="${1:-}"
  if [ "$#" = 0 ]; then
    command /opt/homebrew/bin/mise
    return
  fi
  shift

  case "$command" in
  deactivate|s|shell)
    # if argv doesn't contains -h,--help
    if [[ ! " $@ " =~ " --help " ]] && [[ ! " $@ " =~ " -h " ]]; then
      eval "$(command /opt/homebrew/bin/mise "$command" "$@")"
      return $?
    fi
    ;;
  esac
  command /opt/homebrew/bin/mise "$command" "$@"
}

_mise_hook() {
  eval "$(/opt/homebrew/bin/mise hook-env -s zsh)";
}
typeset -ag precmd_functions;
if [[ -z "${precmd_functions[(r)_mise_hook]+1}" ]]; then
  precmd_functions=( _mise_hook ${precmd_functions[@]} )
fi
typeset -ag chpwd_functions;
if [[ -z "${chpwd_functions[(r)_mise_hook]+1}" ]]; then
  chpwd_functions=( _mise_hook ${chpwd_functions[@]} )
fi

if [ -z "${_mise_cmd_not_found:-}" ]; then
    _mise_cmd_not_found=1
    [ -n "$(declare -f command_not_found_handler)" ] && eval "${$(declare -f command_not_found_handler)/command_not_found_handler/_command_not_found_handler}"

    function command_not_found_handler() {
        if /opt/homebrew/bin/mise hook-not-found -s zsh -- "$1"; then
          _mise_hook
          "$@"
        elif [ -n "$(declare -f _command_not_found_handler)" ]; then
            _command_not_found_handler "$@"
        else
            echo "zsh: command not found: $1" >&2
            return 127
        fi
    }
fi

