"""Run after the process owner creates a session; claim its controlling PTY.

A separate exec helper avoids preexec_fn in the threaded Web process.
"""
import fcntl
import os
import sys
import termios

if __name__ == '__main__':
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
