import time


class Clock:
    def now(self):
        return time.time()


class LeaseClock:
    def wall(self):
        return time.time()

    def monotonic(self):
        return time.monotonic()


class VirtualLeaseClock(LeaseClock):
    def __init__(self, wall, monotonic=1000.0):
        self.wall_value, self.mono_value = float(wall), float(monotonic)

    def wall(self):
        return self.wall_value

    def monotonic(self):
        return self.mono_value

    def advance(self, seconds):
        self.wall_value += seconds
        self.mono_value += max(0, seconds)

    def rollback_wall(self, seconds):
        self.wall_value -= seconds


def lease_expired(stamp, clock, ttl=120):
    """A wall-clock correction/reset can shorten a claim, never extend it.

    Tokens fence old owners. Persisted monotonic values are not treated as
    permanent epochs: a reset expires the claim, and every remaining wait is
    bounded by ttl even when a reboot happens to have a similar uptime.
    """
    if not stamp:
        return True
    wall, mono = clock.wall(), clock.monotonic()
    issued_wall, issued_mono = stamp["wall"], stamp["mono"]
    return (wall < issued_wall or mono < issued_mono or wall-issued_wall >= ttl
            or mono-issued_mono >= ttl)


def lease_stamp(clock):
    return {"wall":clock.wall(),"mono":clock.monotonic()}


class FrozenClock(Clock):
    def __init__(self, value):
        self.value = float(value)

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds
