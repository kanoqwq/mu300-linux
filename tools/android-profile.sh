# Source from the host installer. Return 2 for inconsistent V50 identity,
# so an unsafe platform cannot fall through to the legacy F50 confirmation.
mu300_android_profile() {
    local model device platform
    model=$(printf '%s' "$1" | LC_ALL=C tr 'a-z' 'A-Z' | tr -d '\r')
    device=$(printf '%s' "$2" | LC_ALL=C tr 'a-z' 'A-Z' | tr -d '\r')
    platform=$(printf '%s' "$3" | tr -d '\r')
    case "$model/$device" in
        MU5352/*|*/MU5352)
            [ "$model/$device/$platform" = MU5352/MU5352/ums9620 ] || return 3
            echo u30air ;;
        V50/*|*/V50|MU3351/*|*/MU3351)
            case "$model/$device/$platform" in
                V50/MU3351/ums9620|MU3351/V50/ums9620) echo v50 ;;
                *) return 2 ;;
            esac ;;
        *U30AIR*|*U30_AIR*|*"U30 AIR"*) echo u30air ;;
        *MU300*|*F50*) echo f50 ;;
        *) return 1 ;;
    esac
}
