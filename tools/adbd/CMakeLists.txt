# adbd for MU300 Linux, static (musl): the daemon of AOSP's adb (platform-tools 34.0.5), from the sources
# github.com/nmeum/android-tools vendors (ANDROID_TOOLS: its vendor/ directory). tools/adbd/build.sh runs this.
cmake_minimum_required(VERSION 3.12)
project(mu300-adbd C CXX)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_EXTENSIONS ON)
set(V ${ANDROID_TOOLS})
add_compile_options(-Wno-attributes -Os -ffunction-sections -fdata-sections)
add_compile_definitions(_FILE_OFFSET_BITS=64 _LARGEFILE64_SOURCE _GNU_SOURCE)

add_subdirectory(${V}/boringssl boringssl EXCLUDE_FROM_ALL)
add_subdirectory(${V}/fmtlib fmtlib EXCLUDE_FROM_ALL)

find_package(PkgConfig REQUIRED)
pkg_check_modules(brotlienc REQUIRED libbrotlienc)
pkg_check_modules(brotlidec REQUIRED libbrotlidec)
pkg_check_modules(lz4 REQUIRED liblz4)
pkg_check_modules(zstd REQUIRED libzstd)

add_library(liblog STATIC
	${V}/logging/liblog/log_event_list.cpp
	${V}/logging/liblog/log_event_write.cpp
	${V}/logging/liblog/logger_name.cpp
	${V}/logging/liblog/logger_read.cpp
	${V}/logging/liblog/logger_write.cpp
	${V}/logging/liblog/properties.cpp
	${V}/logging/liblog/logprint.cpp)
target_compile_definitions(liblog PRIVATE -DLIBLOG_LOG_TAG=1006 -D_XOPEN_SOURCE=700 -DFAKE_LOG_DEVICE=1)
target_include_directories(liblog PUBLIC ${V}/core/include ${V}/logging/liblog/include
	${V}/core/libcutils/include ${V}/libbase/include)

add_library(libbase STATIC
	${V}/libbase/abi_compatibility.cpp
	${V}/libbase/chrono_utils.cpp
	${V}/libbase/cmsg.cpp
	${V}/libbase/file.cpp
	${V}/libbase/hex.cpp
	${V}/libbase/logging.cpp
	${V}/libbase/mapped_file.cpp
	${V}/libbase/parsebool.cpp
	${V}/libbase/parsenetaddress.cpp
	${V}/libbase/posix_strerror_r.cpp
	${V}/libbase/process.cpp
	${V}/libbase/properties.cpp
	${V}/libbase/stringprintf.cpp
	${V}/libbase/strings.cpp
	${V}/libbase/threads.cpp
	${V}/libbase/errors_unix.cpp)
target_include_directories(libbase PUBLIC ${V}/libbase/include ${V}/core/include)
target_link_libraries(libbase PUBLIC fmt::fmt liblog)

add_library(libcutils STATIC
	${V}/core/libcutils/android_get_control_file.cpp
	${V}/core/libcutils/canned_fs_config.cpp
	${V}/core/libcutils/fs_config.cpp
	${V}/core/libcutils/fs.cpp
	${V}/core/libcutils/multiuser.cpp
	${V}/core/libcutils/properties.cpp
	${V}/core/libcutils/socket_inaddr_any_server_unix.cpp
	${V}/core/libcutils/socket_local_client_unix.cpp
	${V}/core/libcutils/socket_local_server_unix.cpp
	${V}/core/libcutils/socket_network_client_unix.cpp
	${V}/core/libcutils/sockets_unix.cpp
	${V}/core/libcutils/sockets.cpp
	${V}/core/libcutils/strlcpy.c)
target_include_directories(libcutils PRIVATE ${V}/logging/liblog/include ${V}/core/libutils/include)
target_include_directories(libcutils PUBLIC ${V}/core/libcutils/include ${V}/libbase/include)

set(A ${V}/adb)
add_executable(adbd
	${A}/adb.cpp
	${A}/adb_io.cpp
	${A}/adb_listeners.cpp
	${A}/adb_trace.cpp
	${A}/adb_unique_fd.cpp
	${A}/adb_utils.cpp
	${A}/fdevent/fdevent.cpp
	${A}/fdevent/fdevent_epoll.cpp
	${A}/fdevent/fdevent_poll.cpp
	${A}/services.cpp
	${A}/shell_service_protocol.cpp
	${A}/sockets.cpp
	${A}/socket_spec.cpp
	${A}/sysdeps/env.cpp
	${A}/sysdeps/errno.cpp
	${A}/sysdeps/posix/network.cpp
	${A}/sysdeps_unix.cpp
	${A}/tls/adb_ca_list.cpp
	${A}/tls/tls_connection.cpp
	${A}/transport.cpp
	${A}/transport_fd.cpp
	${A}/types.cpp
	${A}/crypto/key.cpp
	${A}/crypto/rsa_2048_key.cpp
	${A}/crypto/x509_generator.cpp
	${A}/daemon/auth.cpp
	${A}/daemon/file_sync_service.cpp
	${A}/daemon/logging.cpp
	${A}/daemon/main.cpp
	${A}/daemon/services.cpp
	${A}/daemon/shell_service.cpp
	${A}/daemon/transport_local.cpp
	${A}/daemon/usb.cpp
	${A}/daemon/usb_ffs.cpp
	${A}/libs/libadbd_fs/adbd_fs.cpp
	${V}/core/diagnose_usb/diagnose_usb.cpp
	${V}/core/libasyncio/AsyncIO.cpp
	${V}/core/libcrypto_utils/android_pubkey.cpp
	${V}/native/libs/adbd_auth/adbd_auth.cpp
	${CMAKE_CURRENT_SOURCE_DIR}/stubs.cpp)
target_compile_definitions(adbd PRIVATE -DADB_HOST=0 -DADB_VERSION="34.0.5-mu300"
	-DPLATFORM_TOOLS_VERSION="34.0.5" -DANDROID_BASE_UNIQUE_FD_DISABLE_IMPLICIT_CONVERSION=1
	-DPAGE_SIZE=4096)
target_compile_options(adbd PRIVATE -include ${CMAKE_CURRENT_SOURCE_DIR}/include/mu300_compat.h)
target_include_directories(adbd PRIVATE
	${CMAKE_CURRENT_SOURCE_DIR}/include ${A} ${A}/crypto/include ${A}/tls/include ${A}/libs/libadbd_fs/include ${A}/libs/adbconnection/include
	${V}/native/libs/adbd_auth/include ${V}/core/diagnose_usb/include ${V}/core/include
	${V}/core/libasyncio/include ${V}/core/libcrypto_utils/include ${V}/boringssl/include)
target_link_libraries(adbd libbase libcutils liblog ssl crypto
	${brotlienc_STATIC_LIBRARIES} ${brotlidec_STATIC_LIBRARIES} ${lz4_STATIC_LIBRARIES} ${zstd_STATIC_LIBRARIES}
	-static -Wl,--gc-sections -s)
