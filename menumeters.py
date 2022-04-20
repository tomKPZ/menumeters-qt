#!/usr/bin/env python3

from PyQt5.QtCore import QTimer, QLineF
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction
from PyQt5.QtGui import QIcon, QPainter, QPixmap, QColorConstants
from psutil import virtual_memory, cpu_times
from sys import argv, exit
from time import monotonic


def sample_mem():
    mem = virtual_memory()
    return (mem.total - mem.available, mem.available)


def sample_cpu():
    cpu = cpu_times()
    return (cpu.system, cpu.user, cpu.idle)


class TrayIcon():

    def __init__(self, parent, width, height, interval, colors, sample):
        self.width = width
        self.height = height
        self.colors = colors
        self.sample = sample

        self.tray = QSystemTrayIcon(parent)
        self.pixmap = QPixmap(self.width, self.height)
        self.window = [None] * width
        self.draw()

        right_menu = QMenu()
        action = QAction("Exit", right_menu)
        action.triggered.connect(lambda: QApplication.exit(0))
        right_menu.addAction(action)
        self.tray.setContextMenu(right_menu)

        self.tray.show()

        self.timer = QTimer()
        self.timer.timeout.connect(self.timeout)
        self.timer.start(interval)

    def timeout(self):
        sample = self.sample()
        # TODO: use circular buffer
        self.window = self.window[1:] + [sample]
        self.draw()

    def draw(self):
        self.pixmap.fill(QColorConstants.Transparent)
        with QPainter(self.pixmap) as painter:
            painter.setRenderHints(QPainter.Antialiasing)
            for i, sample in enumerate(self.window):
                if sample is None:
                    continue
                total = sum(sample)
                offset = 0
                for val, color in zip(sample, self.colors):
                    painter.setPen(color)
                    height = val / total * self.height
                    painter.drawLine(
                        QLineF(i, self.height - offset, i,
                               self.height - offset - height))
                    offset = height
        self.tray.setIcon(QIcon(self.pixmap))


class DeltaSampler():

    def __init__(self, sampler):
        self.sampler = sampler
        self.prev = self.sampler()
        self.prev_ts = monotonic()

    def __call__(self):
        sample = self.sampler()
        ts = monotonic()
        delta = [(s2 - s1) / (self.prev_ts - ts)
                 for (s1, s2) in zip(self.prev, sample)]
        self.prev, self.prev_ts = sample, ts
        return delta


if __name__ == "__main__":
    app = QApplication(argv)

    cpu = TrayIcon(app, 32, 32, 100,
                   (QColorConstants.Blue, QColorConstants.Cyan,
                    QColorConstants.Transparent), DeltaSampler(sample_cpu))
    mem = TrayIcon(app, 32, 32, 100,
                   (QColorConstants.Green, QColorConstants.Transparent),
                   sample_mem)

    exit(app.exec_())
