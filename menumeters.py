#!/usr/bin/env python3

from itertools import chain
from psutil import virtual_memory, cpu_times, disk_io_counters, net_io_counters
from sys import argv, exit
from time import monotonic
from PyQt5.QtCore import QTimer, QLineF, Qt
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction
from PyQt5.QtGui import QIcon, QPainter, QPixmap, QColorConstants, QTransform, QFont


def sample_mem():
    mem = virtual_memory()
    return (mem.total - mem.available, mem.available)


def sample_cpu():
    cpu = cpu_times()
    return (cpu.system, cpu.user, cpu.idle)


def sample_disk():
    disk = disk_io_counters()
    return (disk.write_bytes, disk.read_bytes)


def sample_net():
    net = net_io_counters()
    return (net.bytes_sent, net.bytes_recv)


def format_bytes(bytes):
    SI_PREFIXES = 'KMGTPEZY'
    for prefix in chain([''], SI_PREFIXES):
        if bytes < 1000:
            break
        bytes /= 1000
    return f'{bytes:.3g}{prefix}B'


class StackedGraph():

    def __init__(self, colors):
        self.colors = colors

    def __call__(self, painter, width, height, samples):
        for i, sample in enumerate(samples):
            total = sum(sample)
            offset = 0
            col = width - i - 1
            for val, color in zip(sample, self.colors):
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

    def __call__(self, painter, width, height, samples):
        self.top(painter, width, height // 2,
                 list(sample[0] for sample in samples))
        painter.setTransform(QTransform().translate(0, height // 2),
                             combine=True)
        self.bottom(painter, width, height // 2,
                    list(sample[1] for sample in samples))


class ScaledGraph():

    def __init__(self, color):
        self.color = color

    def __call__(self, painter, width, height, samples):
        if samples and (total := max(samples)):
            for i, sample in enumerate(samples):
                painter.setPen(self.color)
                val_height = sample / total * height
                col = width - i - 1
                painter.drawLine(QLineF(col, height, col, height - val_height))
        if samples:
            painter.setPen(QColorConstants.White)
            painter.setFont(QFont('monospace', 8))
            painter.drawText(0, 0, width, height, Qt.AlignCenter,
                             format_bytes(samples[0]) + '/s')


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


class DeltaSampler():

    def __init__(self, sampler):
        self.sampler = sampler
        self.prev = self.sampler()
        self.prev_ts = monotonic()

    def __call__(self):
        sample = self.sampler()
        ts = monotonic()
        delta = [(s2 - s1) / (ts - self.prev_ts)
                 for (s1, s2) in zip(self.prev, sample)]
        self.prev, self.prev_ts = sample, ts
        return delta


class TrayIcon():

    def __init__(self, parent, width, height, interval, take_sample, painter):
        self.width = width
        self.height = height
        self.take_sample = take_sample
        self.painter = painter

        self.tray = QSystemTrayIcon(parent)
        self.pixmap = QPixmap(self.width, self.height)
        self.samples = SlidingWindow(width)

        self.timer = QTimer()
        self.timer.timeout.connect(self.timeout)
        self.timer.start(interval)

        right_menu = QMenu()
        action = QAction("Exit", right_menu)
        action.triggered.connect(lambda: QApplication.exit(0))
        right_menu.addAction(action)
        self.tray.setContextMenu(right_menu)

        self.draw()
        self.tray.show()

    def timeout(self):
        self.samples.push(self.take_sample())
        self.draw()

    def draw(self):
        self.pixmap.fill(QColorConstants.Transparent)
        with QPainter(self.pixmap) as painter:
            painter.setRenderHints(QPainter.Antialiasing)
            self.painter(painter, self.width, self.height, self.samples)
        self.tray.setIcon(QIcon(self.pixmap))


if __name__ == "__main__":
    app = QApplication(argv)

    cpu = TrayIcon(
        app, 32, 32, 100, DeltaSampler(sample_cpu),
        StackedGraph((QColorConstants.Blue, QColorConstants.Cyan,
                      QColorConstants.Transparent)))
    mem = TrayIcon(
        app, 32, 32, 100, sample_mem,
        StackedGraph((QColorConstants.Green, QColorConstants.Transparent)))
    disk = TrayIcon(
        app, 32, 32, 100, DeltaSampler(sample_disk),
        SplitGraph(ScaledGraph(QColorConstants.Red),
                   ScaledGraph(QColorConstants.Green)))
    net = TrayIcon(
        app, 32, 32, 100, DeltaSampler(sample_net),
        SplitGraph(ScaledGraph(QColorConstants.Red),
                   ScaledGraph(QColorConstants.Green)))

    exit(app.exec_())
