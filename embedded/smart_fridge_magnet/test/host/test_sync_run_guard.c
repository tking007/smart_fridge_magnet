#include <assert.h>
#include <stdio.h>

#include "wifi_sync_gate.h"

int main(void)
{
    bool in_progress = false;
    assert(wifi_sync_run_try_enter(&in_progress, false));
    assert(in_progress);
    assert(!wifi_sync_run_try_enter(&in_progress, false));
    assert(in_progress);
    wifi_sync_run_leave(&in_progress);
    assert(!in_progress);
    assert(!wifi_sync_run_try_enter(&in_progress, true));
    assert(!in_progress);
    assert(wifi_sync_run_try_enter(&in_progress, false));
    assert(!wifi_sync_run_try_enter(NULL, false));
    wifi_sync_run_leave(NULL);

    puts("sync run guard tests passed");
    return 0;
}
