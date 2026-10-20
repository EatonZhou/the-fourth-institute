"""Linux/macOS PTY implementation. Never imported by Windows Web."""
from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path
import pty
import struct
import sys
import termios

from management_common.process_runtime import spawn_process, terminate_process_tree


class PosixTerminal:
    def __init__(self, argv, *, cwd, cols, rows, env):
        self.master_fd, slave = pty.openpty()
        self.eof = False
        self._closed = False
        self.process = None
        try:
            self.resize(cols, rows)
            os.set_blocking(self.master_fd, False)
            helper = str(Path(__file__).with_name('terminal_posix_exec.py'))
            self.process = spawn_process(
                [sys.executable, helper, *argv], cwd=str(cwd), env=env,
                stdin=slave, stdout=slave, stderr=slave, close_fds=True,
            )
        except BaseException:
            os.close(self.master_fd)
            raise
        finally:
            os.close(slave)
        self.pid = self.process.pid

    def poll(self):
        return self.process.poll()

    def read(self):
        chunks = []
        size = 0
        while True:
            try:
                data = os.read(self.master_fd, 8192)
            except BlockingIOError:
                break
            except OSError as error:
                if error.errno != errno.EIO:
                    raise
                self.eof = True
                break
            if not data:
                self.eof = True
                break
            chunks.append(data)
            size += len(data)
            if size >= 65536 and self.poll() is None:
                break
        return b''.join(chunks)

    def write(self, data: bytes):
        offset = 0
        while offset < len(data):
            written = os.write(self.master_fd, data[offset:])
            if written <= 0:
                raise RuntimeError('terminal input write made no progress')
            offset += written

    def resize(self, cols, rows):
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    def interrupt(self):
        # The terminal driver delivers VINTR to the foreground job, not Web
        # or an unrelated terminal's shell group.
        self.write(b'\x03')

    def close(self):
        if self._closed:
            return
        # Closing the controlling terminal sends HUP to its foreground job.
        if self.master_fd >= 0:
            os.close(self.master_fd)
            self.master_fd = -1
        terminate_process_tree(self.process, timeout=1.5)
        self._closed = True
