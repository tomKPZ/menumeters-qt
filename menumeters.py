#!/usr/bin/env python3

import psutil
import sys
import time
from PyQt5.QtCore import QTimer, QLineF, Qt
from PyQt5.QtGui import (QIcon, QPainter, QPixmap, QColorConstants, QTransform,
                         QFont)
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction


def format_bytes(bytes):
    for prefix in ('', 'K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y'):
        if bytes < 1000:
            break
        bytes /= 1000
    return f'{bytes:.3g}{prefix}B'


class StackedGraph():

    def __init__(self, samples, colors):
        self.samples = samples
        self.colors = colors

    def __call__(self, painter, width, height):
        for i, sample in enumerate(self.samples):
            total = sum(sample)
            offset = 0
            col = width - i - 1
            for color, getter in self.colors:
                val = getter(sample)
                painter.setPen(color)
                val_height = val / total * height
                painter.drawLine(
                    QLineF(col, height - offset, col,
                           height - offset - val_height))
                offset += val_height


class SplitGraph():

    def __init__(self, top, bottom):
        self.top = top
        self.bottom = bottom

    def __call__(self, painter, width, height):
        self.top(painter, width, height // 2)
        painter.setTransform(QTransform().translate(0, height // 2),
                             combine=True)
        self.bottom(painter, width, height // 2)


class ScaledGraph():

    def __init__(self, samples, color, getter):
        self.samples = samples
        self.color = color
        self.getter = getter

    def __call__(self, painter, width, height):
        if self.samples and (total := max(
                self.getter(sample) for sample in self.samples)):
            for i, sample in enumerate(self.samples):
                painter.setPen(self.color)
                val_height = self.getter(sample) / total * height
                col = width - i - 1
                painter.drawLine(QLineF(col, height, col, height - val_height))
        if self.samples:
            painter.setPen(QColorConstants.White)
            painter.setFont(QFont('monospace', 8))
            painter.drawText(
                0, 0, width, height, Qt.AlignCenter,
                format_bytes(self.getter(next(iter(self.samples)))) + '/s')


class SlidingWindow():

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


class RateSample():

    def __init__(self, sampler):
        self.sampler = sampler
        self.prev = self.sampler()
        self.prev_ts = time.monotonic()

    def __call__(self):
        sample = self.sampler()
        ts = time.monotonic()
        delta = [(s2 - s1) / (ts - self.prev_ts)
                 for (s1, s2) in zip(self.prev, sample)]
        self.prev, self.prev_ts = sample, ts
        return type(sample)._make(delta)


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


class TrayIcon():

    def __init__(self, parent, width, height, painter):
        self.width = width
        self.height = height
        self.painter = painter

        self.tray = QSystemTrayIcon(parent)
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
        self.pixmap.fill(QColorConstants.Transparent)
        with QPainter(self.pixmap) as painter:
            painter.setRenderHints(QPainter.Antialiasing)
            self.painter(painter, self.width, self.height)
        self.tray.setIcon(QIcon(self.pixmap))


if __name__ == "__main__":
    app = QApplication(sys.argv)

    cpu = Sampler(100, 32, RateSample(psutil.cpu_times))
    mem = Sampler(100, 32, psutil.virtual_memory)
    disk = Sampler(100, 32, RateSample(psutil.disk_io_counters))
    net = Sampler(100, 32, RateSample(psutil.net_io_counters))

    tray_icons = [
        TrayIcon(
            app, 32, 32,
            StackedGraph(cpu, [
                (QColorConstants.Blue, lambda sample: sample.system),
                (QColorConstants.Cyan, lambda sample: sample.user),
                (QColorConstants.Transparent, lambda sample: sample.idle),
            ])),
        TrayIcon(
            app, 32, 32,
            StackedGraph(mem, [
                (QColorConstants.Green,
                 lambda sample: sample.total - sample.available),
                (QColorConstants.Transparent, lambda sample: sample.available),
            ])),
        TrayIcon(
            app, 32, 32,
            SplitGraph(
                ScaledGraph(disk, QColorConstants.Red,
                            lambda sample: sample.write_bytes),
                ScaledGraph(disk, QColorConstants.Green,
                            lambda sample: sample.read_bytes))),
        TrayIcon(
            app, 32, 32,
            SplitGraph(
                ScaledGraph(net, QColorConstants.Red,
                            lambda sample: sample.bytes_sent),
                ScaledGraph(net, QColorConstants.Green,
                            lambda sample: sample.bytes_recv))),
    ]

    sys.exit(app.exec_())
