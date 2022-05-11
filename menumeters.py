#!/usr/bin/env python3

from psutil import virtual_memory, cpu_times, disk_io_counters
from sys import argv, exit
from time import monotonic
from PyQt5.QtCore import QTimer, QLineF
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction
from PyQt5.QtGui import QIcon, QPainter, QPixmap, QColorConstants


def sample_mem():
    mem = virtual_memory()
    return (mem.total - mem.available, mem.available)


def sample_cpu():
    cpu = cpu_times()
    return (cpu.system, cpu.user, cpu.idle)


def sample_disk():
    disk = disk_io_counters()
    # return (disk.read_bytes, disk.write_bytes)
    return disk.read_bytes,


class StackedGraph():

    def __init__(self, colors):
        self.colors = colors

    def __call__(self, painter, width, height, samples):
        for i, sample in enumerate(samples):
            if sample is None:
                continue
            total = sum(sample)
            offset = 0
            for val, color in zip(sample, self.colors):
                painter.setPen(color)
                val_height = val / total * height
                painter.drawLine(
                    QLineF(i, height - offset, i,
                           height - offset - val_height))
                offset += val_height


class ScaledGraph():

    def __init__(self, color):
        self.color = color

    def __call__(self, painter, width, height, samples):
        try:
            total = max(sample[0] for sample in samples if sample is not None)
        except ValueError:
            return
        if total == 0:
            return
        for i, sample in enumerate(samples):
            if sample is None:
                continue
            painter.setPen(self.color)
            val_height = sample[0] / total * height
            painter.drawLine(QLineF(i, height, i, height - val_height))


class SlidingWindow():

    def __init__(self, size):
        self.start = 0
        self.window = [None] * size

    def push(self, x):
        self.window[self.start] = x
        self.start = (self.start + 1) % len(self.window)

    def __iter__(self):
        for i in range(len(self.window)):
            yield self.window[(self.start + i) % len(self.window)]


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
    disk = TrayIcon(app, 32, 32, 100, DeltaSampler(
        sample_disk), ScaledGraph(QColorConstants.Red))

    exit(app.exec_())
