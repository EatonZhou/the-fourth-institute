"""Google's public gtx transport. No credentials, SDK, or LLM fallback here.

Endpoint/segment decoding follow Translate for Zotero google.ts at
4e9ca763b9d8f07604aceb7671b5e0c29c3b5a3a (AGPL-3.0). This independent
Python transport does not copy its token-generation code or Zotero runtime.
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class GoogleTimeout(TimeoutError):
    def __init__(self, elapsed_ms):
        super().__init__('Google request timed out')
        self.elapsed_ms = elapsed_ms


class GoogleTransport:
    # Abandoned DNS/read calls cannot accumulate unbounded network threads.
    slots = threading.BoundedSemaphore(16)

    def __init__(self, *, opener=urllib.request.urlopen, endpoint='https://translate.google.com'):
        self.opener, self.endpoint = opener, endpoint

    def translate(self, payload, *, timeout, cancelled):
        query = urllib.parse.urlencode(dict(client='gtx', sl=payload['source_language'],
            tl=payload['target_language'], dt='t', q=payload['text']))
        request = urllib.request.Request(self.endpoint + '/translate_a/single?' + query,
            headers={'User-Agent': 'TFI-Paper-Translation/1', 'Accept': 'application/json'})
        if cancelled(): raise InterruptedError('translation cancelled')
        if not self.slots.acquire(blocking=False): raise RuntimeError('Google transport capacity unavailable')
        started, done = threading.Event(), threading.Event()
        state = {}
        def network():
            # Budget begins HERE, immediately before the real HTTP transport.
            state['start'] = time.monotonic(); started.set()
            try:
                with self.opener(request, timeout=timeout) as response:
                    if response.status != 200: raise RuntimeError('Google HTTP error')
                    chunks, size = [], 0
                    while True:
                        if time.monotonic() - state['start'] >= timeout: raise GoogleTimeout(timeout * 1000)
                        read = getattr(response, 'read1', response.read)
                        chunk = read(8192)
                        if not chunk: break
                        size += len(chunk)
                        if size > 256 * 1024: raise ValueError('Google response too large')
                        chunks.append(chunk)
                    data = json.loads(b''.join(chunks))
                    text = ''.join(segment[0] for segment in data[0] if segment and isinstance(segment[0], str))
                    if not text: raise ValueError('Google returned empty translation')
                    state['text'] = text
            except Exception as exc:
                state['error'] = exc
            finally:
                state['finish'] = time.monotonic(); done.set(); self.slots.release()
        try:
            threading.Thread(target=network, name='paper-google-http', daemon=True).start()
        except BaseException:
            self.slots.release(); raise
        started.wait()
        deadline = state['start'] + timeout
        while not done.wait(max(0, min(.05, deadline - time.monotonic()))):
            if cancelled(): raise InterruptedError('translation cancelled')
            if time.monotonic() >= deadline: raise GoogleTimeout((time.monotonic() - state['start']) * 1000)
        elapsed = (state['finish'] - state['start']) * 1000
        if elapsed >= timeout * 1000: raise GoogleTimeout(elapsed)
        if cancelled(): raise InterruptedError('translation cancelled')
        error = state.get('error')
        if isinstance(error, (TimeoutError, urllib.error.URLError)):
            reason = getattr(error, 'reason', error)
            if isinstance(reason, TimeoutError): raise GoogleTimeout(elapsed) from error
        if error: raise error
        return dict(text=state['text'], provider='google', model='translate_a/single:gtx',
                    model_source='service_algorithm', google_request_ms=elapsed)
