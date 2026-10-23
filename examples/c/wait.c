/* Standalone ABI consumer: receive a fixture response using native readiness. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fastls.h"

static void close_request(uint64_t handle) {
    char *result = fastls_close(handle);
    fastls_free(result);
}

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    char *end = NULL;
    unsigned long port = strtoul(argv[1], &end, 10);
    if (*end || port == 0 || port > 65535) return 2;
    char request[256];
    int length = snprintf(request, sizeof(request),
        "{\"url\":\"http://127.0.0.1:%lu/bytes?n=65536\",\"profile\":\"chrome/156/windows\"}", port);
    if (length < 0 || (size_t)length >= sizeof(request)) return 2;
    char *result = fastls_start(request, (size_t)length);
    if (!result) return 3;
    char *field = strstr(result, "\"result\":");
    uint64_t handle = field ? (uint64_t)strtoull(field + 9, NULL, 10) : 0;
    fastls_free(result);
    if (!handle) return 3;
    unsigned int waits = 0;
    int code;
    do { code = fastls_wait(handle, 1, 1000); waits++; } while (code == 1);
    if (code != 0) { close_request(handle); return 4; }
    unsigned char buffer[8192], pattern[] = {0, 1, 128, 255};
    size_t total = 0;
    for (;;) {
        size_t size = 0;
        code = fastls_read(handle, buffer, sizeof(buffer), &size);
        if (code == 0) {
            for (size_t i = 0; i < size; i++) {
                if (buffer[i] != pattern[(total + i) % 4]) { close_request(handle); return 5; }
            }
            total += size;
        } else if (code == 1) {
            code = fastls_wait(handle, 2, 1000);
            waits++;
            if (code < 0) { close_request(handle); return 6; }
        } else if (code == 2) {
            break;
        } else { close_request(handle); return 7; }
    }
    close_request(handle);
    if (total != 65536 || fastls_wait(handle, 2, 0) != -2) return 8;
    printf("{\"ok\":true,\"bytes\":%zu,\"waits\":%u}\n", total, waits);
    return 0;
}
