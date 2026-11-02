# Wait for asynchronous MMC discovery, then refresh PARTNAME aliases.
# A populated sysfs node is not necessarily a populated /dev/block/by-name.
u30pro_wait_storage() {
    local remaining=100 disk misc_node root_node mode
    mode=$(cat /etc/mu300-desktop-root-mode)
    while [ "$remaining" -gt 0 ]; do
        /usr/sbin/u30pro-nodes --populate-nodes || return 1
        disk=$(mu300_storage_disk) &&
        misc_node=$(mu300_storage_part misc "$disk") &&
        [ -b "/dev/$misc_node" ] && {
            case "$mode" in
                part) root_node=$(mu300_storage_part mu300root "$disk") ;;
                prepare|install-part|data-image) root_node=$(mu300_storage_part userdata "$disk") ;;
                sd) root_node=$misc_node ;;
                *) return 1 ;;
            esac
            [ -n "$root_node" ] && [ -b "/dev/$root_node" ] && return 0
        }
        remaining=$((remaining - 1))
        sleep 0.1
    done
    return 1
}
