"""Bounded progress output for installation file operations."""
import json
import time


def emit(message):
    print('@TFI_PROGRESS ' + json.dumps({'message': message}, ensure_ascii=False), flush=True)


class FileProgress:
    def __init__(self, label, total_files, total_bytes, callback=None):
        self.label = label
        self.total_files, self.total_bytes = total_files, total_bytes
        self.files = self.bytes = 0
        self.callback = callback or emit
        self.last = 0
        self.report()

    def advance(self, size):
        self.files += 1
        self.bytes += size
        if self.files == self.total_files or time.monotonic() - self.last >= 0.25:
            self.report()

    def report(self):
        self.last = time.monotonic()
        percent = int(100 * self.bytes / self.total_bytes) if self.total_bytes else (100 if self.files == self.total_files else 0)
        self.callback(f'{self.label}：{self.files}/{self.total_files} 个文件'
                      f'（{percent}%，{self.bytes / 1048576:.1f}/{self.total_bytes / 1048576:.1f} MiB）')
