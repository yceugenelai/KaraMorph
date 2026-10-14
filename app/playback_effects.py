"""Background PCM rendering with a bounded queue and source-time mapping."""
import queue
import threading
import numpy as np
from app.soundtouch import SoundTouch


class PlaybackRenderer:
    def __init__(self, backing, guide, rate, start=0, settings=(1., 0), factory=SoundTouch):
        self.backing, self.guide = backing, guide
        self.rate = rate
        self.settings = settings
        self.factory = factory
        self.blocks = queue.Queue(maxsize=4)
        self.cancel = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.position = float(start)
        self.pending = None
        self.offset = 0
        self.ended = False
        self.underflows = 0
        self.start = start
        self.thread = threading.Thread(target=self._render, daemon=True, name='KaraMorph-SoundTouch')
        self.thread.start()
        if not self.ready.wait(5):
            self.close()
            raise RuntimeError('SoundTouch preparation timed out')
        if self.error:
            self.close()
            raise RuntimeError(self.error)

    def _push(self, samples, position, tempo):
        for start in range(0, len(samples), 1024):
            block = samples[start:start+1024].copy()
            while not self.cancel.is_set():
                try:
                    self.blocks.put((block, position + start * tempo, tempo), timeout=.05)
                    if self.blocks.qsize() >= 3:
                        self.ready.set()
                    break
                except queue.Full:
                    continue
            if self.cancel.is_set():
                return

    def _render(self):
        processor = None
        position = float(self.start)
        channels = self.backing.shape[1]
        last_output = None
        previous = (1., 0)
        transition = False
        try:
            for start in range(self.start, len(self.backing), 1024):
                if self.cancel.is_set():
                    return
                count = min(1024, len(self.backing)-start)
                samples = np.zeros((count, channels * 2), np.float32)
                samples[:, :channels] = self.backing[start:start+count]
                if self.guide is not None:
                    vocal = self.guide[start:start+count]
                    samples[:len(vocal), channels:] = vocal
                tempo, key = self.settings
                if (tempo, key) != previous:
                    transition = True
                    previous = (tempo, key)
                if processor is None and (tempo != 1 or key != 0):
                    processor = self.factory(self.rate, channels * 2)
                if processor:
                    processor.configure(tempo, key)
                    processor.put(samples)
                    while not self.cancel.is_set():
                        output = processor.receive()
                        if not len(output):
                            break
                        if transition and last_output is not None:
                            count_fade = min(len(output), max(1, round(self.rate*.004)))
                            weights = np.linspace(0., 1., count_fade, dtype=np.float32)[:, None]
                            output[:count_fade] = last_output * (1.-weights) + output[:count_fade] * weights
                        transition = False
                        last_output = output[-1].copy()
                        self._push(output, position, tempo)
                        position += len(output)*tempo
                else:
                    self._push(samples, position, 1.)
                    position += len(samples)
                    last_output = samples[-1].copy()
            if processor and not self.cancel.is_set():
                processor.flush()
                while not self.cancel.is_set():
                    output = processor.receive()
                    if not len(output):
                        break
                    self._push(output, position, tempo)
                    position += len(output)*tempo
        except Exception as exc:
            self.error = str(exc)
        finally:
            if processor:
                processor.close()
            while not self.cancel.is_set():
                try:
                    self.blocks.put(None, timeout=.05)
                    break
                except queue.Full:
                    self.ready.set()
            self.ready.set()

    def read_into(self, backing, guide, frames):
        backing.fill(0)
        guide.fill(0)
        written = 0
        while written < frames:
            if self.pending is None:
                try:
                    self.pending = self.blocks.get_nowait()
                    self.offset = 0
                except queue.Empty:
                    self.underflows += 1
                    break
                if self.pending is None:
                    self.ended = True
                    self.position = float(len(self.backing))
                    break
            samples, position, tempo = self.pending
            count = min(frames-written, len(samples)-self.offset)
            channels = backing.shape[1]
            backing[written:written+count] = samples[self.offset:self.offset+count, :channels]
            guide[written:written+count] = samples[self.offset:self.offset+count, channels:]
            self.offset += count
            written += count
            self.position = min(float(len(self.backing)), position + self.offset * tempo)
            if self.offset == len(samples):
                self.pending = None
        return written

    def close(self):
        self.cancel.set()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise RuntimeError('SoundTouch worker did not stop')
