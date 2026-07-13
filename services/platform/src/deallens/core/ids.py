"""UUIDv7 (RFC 9562) generation — time-ordered PKs for index locality (§11.1).

Hand-rolled because the stdlib `uuid` module has no `uuid7()` on Python 3.12; swap for
`uuid.uuid7()` once the project's minimum Python version adopts it.
"""

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    unix_ms = time.time_ns() // 1_000_000
    rand = os.urandom(10)

    b = bytearray(16)
    b[0:6] = unix_ms.to_bytes(6, "big")
    b[6] = 0x70 | (rand[0] & 0x0F)  # version 7 in top nibble of byte 6
    b[7] = rand[1]
    b[8] = 0x80 | (rand[2] & 0x3F)  # variant 10xxxxxx
    b[9:16] = rand[3:10]

    return uuid.UUID(bytes=bytes(b))
