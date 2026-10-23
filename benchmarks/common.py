"""Bounded metric aggregation and benchmark cases; no client is imported here."""
from collections import Counter
import math

SLAB = bytes([0, 1, 128, 255]) * 16384
EVENT = b"data: " + b"x" * 56 + b"\n\n"
LOG_STEP = math.log(1.02)


class Histogram:
    """Upper-bound quantiles with 2% multiplicative buckets, in seconds."""
    def __init__(self):
        self.buckets = Counter()

    def add(self, value):
        self.buckets[int(math.log1p(max(0, value) * 1e6) / LOG_STEP)] += 1

    def percentile(self, fraction):
        target = math.ceil(sum(self.buckets.values()) * fraction)
        if not target:
            return None
        cumulative = 0
        for index, count in sorted(self.buckets.items()):
            cumulative += count
            if cumulative >= target:
                return math.expm1((index + 1) * LOG_STEP) / 1e6


def cases(profile):
    smoke = profile == "smoke"
    size = 256 * 1024 if smoke else 64 * 1024 * 1024
    concurrency = 2 if smoke else 32
    result = []
    def put(name, protocol, mode, clients, **options):
        base = dict(name=name, protocol=protocol, mode=mode, clients=clients,
                    concurrency=1, workload="small", size=1024, cold=False,
                    browser=False, gzip=False, poll=False)
        base.update(options); result.append(base)
    for protocol, mode, clients in (("h1","sync",["requests","curl","fastls"]),
                                    ("h1","async",["curl","fastls"]),
                                    ("h2","async",["curl","fastls"])):
        for count in (1, concurrency):
            for cold in (False, True):
                put(f"small-{protocol}-{mode}-c{count}-{'cold' if cold else 'warm'}",protocol,mode,clients,concurrency=count,cold=cold)
    for protocol, mode, clients in (("h1","sync",["requests","curl","fastls"]),("h2","async",["curl","fastls"])):
        for count in (1, 2 if smoke else 8):
            put(f"download-{protocol}-c{count}",protocol,mode,clients,concurrency=count,workload="download",size=size)
        for workload in ("gzip", "slow", "idle", "upload"):
            put(f"{workload}-{protocol}",protocol,mode,clients,workload=workload,size=size,
                concurrency=concurrency if workload=="idle" else 1,gzip=workload=="gzip")
    put("upload-h1-async","h1","async",["curl","fastls"],workload="upload",size=size)
    for count in (1,concurrency):
        put(f"browser-h2-c{count}","h2","async",["curl","fastls"],browser=True,concurrency=count)
    for workload in ("small","idle"):
        for poll in (False,True):
            put(f"notifications-{workload}-{'poll' if poll else 'native'}","h2","async",["fastls"],
                workload=workload,concurrency=concurrency,poll=poll)
    put("sse-parser-h2","h2","async",["fastls"],workload="parsed",concurrency=concurrency)
    put("curl-iterator-small-h1","h1","sync",["curl-stream"],allow_nonreuse=True)
    put("curl-iterator-slow-h1","h1","sync",["curl-stream"],workload="slow",size=size,allow_nonreuse=True)
    return result
