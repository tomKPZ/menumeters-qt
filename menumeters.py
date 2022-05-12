#!/usr/bin/env python3

import sys
import time

import psutil
from PyQt5.QtCore import QLineF, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QTransform
from PyQt5.QtWidgets import QAction, QApplication, QMenu, QSystemTrayIcon


def format_bytes(bytes):
    for prefix in ("", "K", "M", "G", "T", "P", "E", "Z", "Y"):
        if bytes < 1000:
            break
        bytes /= 1000
    return f"{bytes:.3g}", f"{prefix}B"


class Graph:
    def __init__(self, samples, colors):
        self.samples = samples
        self.colors = colors

    def __call__(self, painter, width, height):
        if not self.samples:
            return
        total = max(sum(sample) for sample in self.samples)
        if total == 0:
            return
        scale = 1 / total
        for i, sample in enumerate(self.samples):
            total = sum(sample)
            offset = 0
            col = width - i - 1
            for color, val in zip(self.colors, sample):
                painter.setPen(QColor.fromRgba(color))
                val_height = val * scale * height
                painter.drawLine(
                    QLineF(
                        col, height - offset, col, height - offset - val_height
                    )
                )
                offset += val_height


class Text:
    def __init__(self, samples, formatter, font, size, color, flags):
        self.samples = samples
        self.formatter = formatter
        self.font = font
        self.size = size
        self.color = color
        self.flags = flags

    def __call__(self, painter, width, height):
        if not self.samples:
            return

        painter.setPen(QColor.fromRgba(self.color))
        painter.setFont(QFont(self.font, self.size))
        painter.drawText(
            0,
            0,
            width,
            height,
            self.flags,
            self.formatter(next(iter(self.samples))),
        )


class VSplit:
    def __init__(self, top, bottom):
        self.top = top
        self.bottom = bottom

    def __call__(self, painter, width, height):
        self.top(painter, width, height // 2)
        painter.save()
        painter.setTransform(
            QTransform().translate(0, height // 2), combine=True
        )
        self.bottom(painter, width, height // 2)
        painter.restore()


class Overlay:
    def __init__(self, top, bottom):
        self.top = top
        self.bottom = bottom

    def __call__(self, painter, width, height):
        self.bottom(painter, width, height)
        self.top(painter, width, height)


class SlidingWindow:
    def __init__(self, size):
        self.len = 0
        self.end = 0
        self.window = [None] * size

    def push(self, x):
        self.window[self.end] = x
        self.end = (self.end + 1) % len(self.window)
        self.len = min(len(self.window), self.len + 1)

    def __len__(self):
        return self.len

    def __iter__(self):
        for i in range(self.len):
            yield self.window[(self.end - i - 1) % len(self.window)]


class Rate:
    def __init__(self, sampler):
        self.sampler = sampler
        self.prev = self.sampler()
        self.prev_ts = time.monotonic()

    def __call__(self):
        sample = self.sampler()
        ts = time.monotonic()
        rate = [
            (s2 - s1) / (ts - self.prev_ts)
            for (s1, s2) in zip(self.prev, sample)
        ]
        self.prev, self.prev_ts = sample, ts
        return rate


class Delta:
    def __init__(self, sampler):
        self.sampler = sampler
        self.prev = self.sampler()

    def __call__(self):
        sample = self.sampler()
        rate = [(s2 - s1) for (s1, s2) in zip(self.prev, sample)]
        self.prev = sample
        return rate


class Normalize:
    def __init__(self, sampler):
        self.sampler = sampler

    def __call__(self):
        sample = self.sampler()
        total = sum(sample)
        return [x / total for x in sample]


class Sampler(SlidingWindow):
    def __init__(self, interval, window, sample):
        super().__init__(window)

        self.sample = sample

        self.timer = QTimer()
        self.timer.timeout.connect(self.timeout)
        self.timer.start(interval)

    def timeout(self):
        self.push(self.sample())

        # TODO: Redraw only the icons that are necessary.
        for icon in tray_icons:
            icon.draw()


class Index:
    def __init__(self, sampler, index):
        self.sampler = sampler
        self.index = index

    def __len__(self):
        return len(self.sampler)

    def __iter__(self):
        for sample in self.sampler:
            yield sample[self.index]


class List:
    def __init__(self, sampler):
        self.sampler = sampler

    def __len__(self):
        return len(self.sampler)

    def __iter__(self):
        for sample in self.sampler:
            yield [sample]


class TrayIcon:
    def __init__(self, width, height, painter):
        self.width = width
        self.height = height
        self.painter = painter

        self.tray = QSystemTrayIcon()
        self.pixmap = QPixmap(self.width, self.height)
        self.samples = SlidingWindow(width)

        right_menu = QMenu()
        action = QAction("Exit", right_menu)
        action.triggered.connect(lambda: QApplication.exit(0))
        right_menu.addAction(action)
        self.tray.setContextMenu(right_menu)

        self.draw()
        self.tray.show()

    def draw(self):
        self.pixmap.fill(QColor.fromRgba(0))
        with QPainter(self.pixmap) as painter:
            painter.setRenderHints(QPainter.Antialiasing)
            self.painter(painter, self.width, self.height)
        self.tray.setIcon(QIcon(self.pixmap))


if __name__ == "__main__":
    app = QApplication(sys.argv)

    def cpu_sample():
        cpu = psutil.cpu_times()
        return cpu.system, cpu.user, cpu.idle

    def mem_sample():
        mem = psutil.virtual_memory()
        return mem.total - mem.available, mem.available

    def disk_sample():
        disk = psutil.disk_io_counters()
        return disk.write_bytes, disk.read_bytes

    def net_sample():
        net = psutil.net_io_counters()
        return net.bytes_sent, net.bytes_recv

    cpu = Sampler(100, 32, Normalize(Delta(cpu_sample)))
    mem = Sampler(100, 32, mem_sample)
    disk = Sampler(100, 32, Rate(disk_sample))
    net = Sampler(100, 32, Rate(net_sample))

    disk_write = Index(disk, 0)
    disk_read = Index(disk, 1)
    net_sent = Index(net, 0)
    net_recv = Index(net, 1)

    text_format = {
        "font": "monospace",
        "size": 10,
        "color": 0xFFFFFFFF,
    }
    rate = text_format | {
        "formatter": lambda sample: format_bytes(sample)[0],
        "flags": Qt.AlignRight | Qt.AlignVCenter,
    }
    rate_units = text_format | {
        "formatter": lambda sample: format_bytes(sample)[1] + "/s",
        "flags": Qt.AlignLeft | Qt.AlignVCenter,
    }

    tray_icons = [
        TrayIcon(32, 32, Graph(cpu, [0xFF0000FF, 0xFF00FFFF, 0x00000000])),
        TrayIcon(32, 32, Graph(mem, [0xFF00FF00, 0x00000000])),
        TrayIcon(
            32,
            32,
            VSplit(
                Graph(List(disk_write), [0xFFFF0000]),
                Graph(List(disk_read), [0xFF00FF00]),
            ),
        ),
        TrayIcon(
            32, 32, VSplit(Text(disk_write, **rate), Text(disk_read, **rate))
        ),
        TrayIcon(
            32,
            32,
            VSplit(
                Text(disk_write, **rate_units), Text(disk_read, **rate_units)
            ),
        ),
        TrayIcon(
            32,
            32,
            VSplit(
                Graph(List(net_sent), [0xFFFF0000]),
                Graph(List(net_recv), [0xFF00FF00]),
            ),
        ),
        TrayIcon(
            32, 32, VSplit(Text(net_sent, **rate), Text(net_recv, **rate))
        ),
        TrayIcon(
            32,
            32,
            VSplit(Text(net_sent, **rate_units), Text(net_recv, **rate_units)),
        ),
    ]

    sys.exit(app.exec_())
