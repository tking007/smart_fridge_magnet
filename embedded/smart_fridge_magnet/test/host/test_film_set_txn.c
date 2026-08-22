#include <assert.h>
#include <stdio.h>

#include "film_set_txn.h"

int main(void)
{
    assert(film_set_name_is_safe("1_today.film"));
    assert(film_set_name_is_safe("A-b_c.9.film"));
    assert(!film_set_name_is_safe(NULL));
    assert(!film_set_name_is_safe(""));
    assert(!film_set_name_is_safe("../today.film"));
    assert(!film_set_name_is_safe("dir/today.film"));
    assert(!film_set_name_is_safe("today film.film"));
    assert(!film_set_name_is_safe("today.bin"));

    assert(film_set_recovery_action(false, true, true) ==
           FILM_SET_RECOVERY_RESTORE_BACKUP);
    assert(film_set_recovery_action(false, true, false) ==
           FILM_SET_RECOVERY_KEEP);
    assert(film_set_recovery_action(false, false, false) ==
           FILM_SET_RECOVERY_REMOVE_TARGET);
    assert(film_set_recovery_action(true, true, true) ==
           FILM_SET_RECOVERY_KEEP);
    assert(film_set_recovery_action(true, false, false) ==
           FILM_SET_RECOVERY_KEEP);

    puts("film set transaction tests passed");
    return 0;
}
