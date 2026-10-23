#include <stdio.h>
#include "fastls.h"

int main(void) {
    if (fastls_abi_version() != 1) return 1;
    char *result = fastls_profiles();
    if (!result) return 2;
    puts(result);
    fastls_free(result);
    return 0;
}
