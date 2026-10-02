"""Close synchronous inference iterators on ASGI disconnect, including prefill."""
import anyio
from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool


def event_response(request: Request, events) -> StreamingResponse:
    async def body():
        try:
            while True:
                frame = await run_in_threadpool(next, events, None)
                if frame is None or await request.is_disconnected():
                    break
                yield frame
        finally:
            # ASGI cancellation must not skip worker cleanup.
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(events.close)
    return StreamingResponse(body(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
