#!/bin/sh
# During a transaction, RPC uses the isolated interpreter, never half-updated
# live Python libraries. The persistent pointer is ignored after RAM is gone.
r=$(cat /etc/unisoc-ota/active-runtime 2>/dev/null)
case "$r" in
    /tmp/unisoc-ota-*/root)
        id=${r#/tmp/unisoc-ota-}; id=${id%/root}
        case "$id" in *[!0-9a-f]*|'') ;;
            *) if [ ${#id} -eq 64 ] && [ -x "$r/ota/busybox" ] && [ -f "$r/ota/rpc.py" ]; then
                export UNISOC_OTA_ROOT=/target UNISOC_OTA_RAM=/ UNISOC_OTA_LIVE=1 PYTHONHOME=/usr
                exec "$r/ota/busybox" chroot "$r" /usr/bin/python3 /ota/rpc.py "$@"
            fi ;;
        esac ;;
esac
exec python3 /usr/lib/unisoc-ota/rpc.py "$@"
