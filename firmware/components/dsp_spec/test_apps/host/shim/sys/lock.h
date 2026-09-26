/** newlib locks as no-ops: the host test runs on one thread.
 *  @ctx any | non-blocking
 */
#pragma once

typedef int _lock_t;

#define _lock_init(lock) ((void)(lock))
#define _lock_acquire(lock) ((void)(lock))
#define _lock_release(lock) ((void)(lock))
