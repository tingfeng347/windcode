#!/bin/sh
set -eu

workspace=/workspace
workspace_uid=$(stat -c '%u' "$workspace")
workspace_gid=$(stat -c '%g' "$workspace")

# Docker assigns TERM=xterm for an allocated pseudo-TTY. That makes Rich/Textual
# select the 16-color "standard" palette, losing the welcome screen's branding.
# Preserve an explicitly supplied terminal type, but upgrade Docker's default.
if [ "${TERM-}" = "xterm" ]; then
    export TERM=xterm-256color
fi
if [ -z "${COLORTERM-}" ]; then
    export COLORTERM=truecolor
fi

# Keep user-scoped Windcode state writable after dropping root privileges. This
# directory is in the image unless the caller deliberately mounts a volume there.
chown "$workspace_uid:$workspace_gid" "$HOME"

# `docker run IMAGE /workspace` and `docker run IMAGE --help` are convenient
# shortcuts for the Windcode CLI; arbitrary commands remain available for diagnosis.
if [ "$#" -eq 0 ]; then
    set -- windcode "$workspace"
elif [ "$1" != "windcode" ] && { [ -d "$1" ] || [ "${1#-}" != "$1" ]; }; then
    set -- windcode "$@"
fi

exec setpriv \
    --reuid="$workspace_uid" \
    --regid="$workspace_gid" \
    --clear-groups \
    -- "$@"
