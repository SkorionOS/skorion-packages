#!/bin/sh
UUID_SRC="/tmp/uu/.uuplugin_uuid"

# Parse install dir from ExecStart line in the service file
INSTALL_DIR=$(systemctl cat uuplugin 2>/dev/null \
    | grep 'ExecStart=.*uuplugin_monitor' \
    | sed 's|.*ExecStart=/bin/sh ||; s|/uuplugin_monitor.sh.*||')

[ -z "$INSTALL_DIR" ] && exit 1
UUID_BAK="${INSTALL_DIR}/.uuplugin_uuid.bak"

case "$1" in
    restore)
        mkdir -p /tmp/uu
        [ -f "$UUID_BAK" ] && cp "$UUID_BAK" "$UUID_SRC"
        ;;
    backup)
        [ -f "$UUID_SRC" ] && cp "$UUID_SRC" "$UUID_BAK"
        ;;
esac
