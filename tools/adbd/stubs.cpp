// MU300 Linux adbd: the Android-only services that are left out of this build.
//   JDWP (debugging Java processes: there are none), adb over Wi-Fi with pairing (needs Android's framework and
//   would open a network port) and mDNS. Leaving them out also leaves out protobuf.
#include <string.h>

#include <memory>

#include <openssl/base64.h>

#include "adb_unique_fd.h"
#include "adb_wifi.h"
#include "daemon/jdwp_service.h"
#include "daemon/mdns.h"
#include "socket.h"

int init_jdwp(void) { return 0; }
asocket* create_jdwp_service_socket() { return nullptr; }
asocket* create_jdwp_tracker_service_socket() { return nullptr; }
asocket* create_app_tracker_service_socket() { return nullptr; }
unique_fd create_jdwp_connection_fd(int) { return unique_fd{}; }

void adbd_wifi_init(AdbdAuthContext*) {}
void adbd_wifi_secure_connect(atransport*) {}

void setup_mdns(int) {}

// musl has no b64_pton (mu300_compat.h); the keys in adb_keys are "BASE64 comment" lines, split before this
int b64_pton(const char* src, unsigned char* target, size_t targsize) {
    size_t len = strlen(src), max = 0, out = 0;
    if (!EVP_DecodedLength(&max, len)) return -1;
    std::unique_ptr<uint8_t[]> buf(new uint8_t[max + 1]);
    if (!EVP_DecodeBase64(buf.get(), &out, max, reinterpret_cast<const uint8_t*>(src), len)) return -1;
    if (out > targsize) return -1;
    memcpy(target, buf.get(), out);
    return static_cast<int>(out);
}
