// MU300 Linux adbd: soong's build/version.h (adb --version prints this as the build)
#pragma once

#include <string>

namespace android {
namespace build {
inline std::string GetBuildNumber() { return "mu300-linux"; }
}  // namespace build
}  // namespace android
