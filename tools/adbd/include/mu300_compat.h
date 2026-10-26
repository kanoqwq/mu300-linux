// MU300 Linux adbd: what bionic and glibc have and musl has not (included in every file of adbd itself)
#pragma once

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif
// <resolv.h>'s b64_pton: decode the base64 string SRC into at most TARGSIZE bytes of TARGET; the number of bytes,
// -1 when SRC is not base64 or does not fit
int b64_pton(const char* src, unsigned char* target, size_t targsize);
#ifdef __cplusplus
}
#endif
