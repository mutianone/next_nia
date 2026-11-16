"""Bounded transient retries. No retries for auth, schema or evidence errors."""
import time
import threading

_gate = threading.Lock()
_next = 0.0

def reserve():
    global _next
    with _gate:
        delay=max(0.0,_next-time.monotonic())
        _next=max(_next,time.monotonic())+2.0
    if delay:time.sleep(delay)

def retry(operation, on_retry=None):
    for attempt in range(3):
        reserve()
        try:
            return operation()
        except Exception as error:
            status=getattr(error,'status',None)
            if status not in (429,503,504) or attempt==2:raise
            delay=65*(attempt+1) if status==429 else 3*(attempt+1)
            if on_retry:on_retry(attempt+1,delay,status)
            time.sleep(delay)
