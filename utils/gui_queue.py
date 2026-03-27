"""
utils/gui_queue.py
──────────────────
A minimal thread-safe queue that lets worker threads post GUI-update
callables to be executed on the main (DPG) thread in the render loop.

Usage
-----
  # From a worker thread:
  from utils.gui_queue import post
  post(lambda: dpg.add_text("hello", parent="some_group"))

  # From the main render loop:
  from utils.gui_queue import drain
  while dpg.is_dearpygui_running():
      drain()
      dpg.render_dearpygui_frame()
"""
import queue as _queue_module

_q: _queue_module.Queue = _queue_module.Queue()


def post(fn) -> None:
    """Enqueue *fn* (a zero-argument callable) to run on the main thread."""
    _q.put(fn)


def drain() -> None:
    """
    Execute all queued callables.
    Must be called from the DPG main thread only.
    """
    while True:
        try:
            fn = _q.get_nowait()
            fn()
        except _queue_module.Empty:
            break
        except Exception as exc:
            # Never let a bad callable crash the render loop
            print(f"[gui_queue] Error executing queued fn: {exc}")
