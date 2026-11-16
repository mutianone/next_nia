"""Optional standalone durable audio worker: python -m backend.app.capture_worker.
Start the web service with CRJ_CAPTURE_EMBEDDED=0 when using this process.
"""
import os
from pathlib import Path
import signal
import threading
from .config import SettingsStore
from .database import application_engine,Base
from .record_capture import worker

def main():
    directory=Path(os.environ.get('CRJ_DATA_DIR') or Path(__file__).resolve().parents[1]/'data').resolve()
    store=SettingsStore(directory);settings=store.load();engine=application_engine(settings,directory)
    Base.metadata.create_all(engine)
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
    threads=[threading.Thread(target=worker,args=(engine,store,stop)) for _ in range(2)]
    for thread in threads:thread.start()
    try:
        while not stop.wait(5):
            with store.lock:store.load()
    finally:
        stop.set()
        for thread in threads:thread.join()
        engine.dispose()
if __name__=='__main__':main()
