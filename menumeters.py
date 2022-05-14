#!/usr/bin/env python3

import sys
import time

import psutil
from PyQt5.QtCore import QLineF, Qt, QTimer
from PyQt5.QtGui import (QColor, QColorConstants, QFont, QIcon, QPainter,
                         QPixmap, QTransform)
from PyQt5.QtWidgets import QAction, QApplication, QMenu, QSystemTrayIcon


def format_bytes(bytes):
    for prefix in ("", "K", "M", "G", "T", "P", "E", "Z", "Y"):
        if bytes < 1000:
            break
        bytes /= 1000
    return f"{bytes:4.3g}", f"{prefix}B"


def normalize(sample):
    total = sum(sample)
    return [x / total for x in sample]


class SlidingWindow:
    def __init__(self, size):
        self.len = 0
        self.end = 0
        self.window = [None] * size

    def push(self, x):
        self.window[self.end] = x
        self.end = (self.end + 1) % len(self.window)
        self.len = min(len(self.window), self.len + 1)

    def __iter__(self):
        for i in range(self.len):
            yield self.window[(self.end - i - 1) % len(self.window)]


class Graph:
    def __init__(self, samples, colors):
        self.samples = samples
        self.colors = colors

    def __call__(self, painter, width, height):
        try:
            scale = 1 / max(sum(sample) for sample in self.samples)
        except (ValueError, ZeroDivisionError):
            return
        for i, sample in enumerate(self.samples):
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

    def contains(self, x):
        return self.samples.contains(x)


class Text:
    def __init__(self, samples, formatter, font, size, color, flags):
        self.samples = samples
        self.formatter = formatter
        self.font = font
        self.size = size
        self.color = color
        self.flags = flags

    def __call__(self, painter, width, height):
        if next(iter(self.samples), None) is None:
            return

        text = self.formatter(next(iter(self.samples)))
        painter.setPen(QColor.fromRgba(self.color))
        painter.setFont(QFont(self.font, self.size))
        painter.drawText(0, 0, width, height, self.flags, text)

    def contains(self, x):
        return self.samples.contains(x)


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

    def contains(self, x):
        return self.top.contains(x) or self.bottom.contains(x)


class Overlay:
    def __init__(self, top, bottom):
        self.top = top
        self.bottom = bottom

    def __call__(self, painter, width, height):
        self.bottom(painter, width, height)
        self.top(painter, width, height)

    def contains(self, x):
        return self.top.contains(x) or self.bottom.contains(x)


class Store:
    def __init__(self, sampler):
        self.sampler = sampler
        self.prev = None

    def __call__(self):
        self.prev = self.sampler()
        return self.prev


class Sampler:
    def __init__(self, interval, window, sample, convert):
        self.window = SlidingWindow(window)
        self.sample = sample
        self.convert = convert

        self.timer = QTimer()
        self.timer.timeout.connect(self.timeout)
        self.timer.start(interval)
        self.window.push(self.convert(self.sample()))

    def timeout(self):
        self.window.push(self.convert(self.sample()))

        for icon in tray_icons:
            if icon.painter.contains(self):
                icon.draw()

    def __iter__(self):
        return iter(self.window)

    def contains(self, x):
        return self is x


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
        return type(sample)._make(rate)


class Index:
    def __init__(self, sampler, index):
        self.sampler = sampler
        self.index = index

    def __iter__(self):
        for sample in self.sampler:
            yield sample[self.index]

    def contains(self, x):
        return self.sampler.contains(x)


class List:
    def __init__(self, sampler):
        self.sampler = sampler

    def __iter__(self):
        for sample in self.sampler:
            yield [sample]

    def contains(self, x):
        return self.sampler.contains(x)


class TrayIcon:
    def __init__(self, width, height, painter, menuitems):
        self.width = width
        self.height = height
        self.painter = painter
        self.menuitems = menuitems

        self.tray = QSystemTrayIcon()
        self.pixmap = QPixmap(self.width, self.height)

        self.right_menu = QMenu()
        for text in self.menuitems():
            self.right_menu.addAction(text)
        self.right_menu.addSeparator()
        action = QAction("Exit", self.right_menu)
        action.triggered.connect(lambda: QApplication.exit(0))
        self.right_menu.addAction(action)
        self.tray.setContextMenu(self.right_menu)

        self.draw()
        self.tray.show()

    def draw(self):
        self.pixmap.fill(QColorConstants.Transparent)
        with QPainter(self.pixmap) as painter:
            painter.setRenderHints(QPainter.Antialiasing)
            self.painter(painter, self.width, self.height)
        self.tray.setIcon(QIcon(self.pixmap))

        for text, action in zip(self.menuitems(), self.right_menu.actions()):
            action.setText(text)
            action.setEnabled(False)


if __name__ == "__main__":
    app = QApplication(sys.argv)

    cpu_sample = Store(Rate(psutil.cpu_times))
    mem_sample = Store(psutil.virtual_memory)
    disk_sample = Store(Rate(psutil.disk_io_counters))
    net_sample = Store(Rate(psutil.net_io_counters))

    def format_name(name):
        return name.title().replace("_", " ")

    def menu_bytes(val):
        bytes, units = format_bytes(val)
        if len(units) == 1:
            bytes = " " + bytes
        return f"{bytes}{units}"

    def cpu_menu():
        n = psutil.cpu_count()
        for name, val in cpu_sample.prev._asdict().items():
            yield f"{val/n:6.1%} {format_name(name)}"

    def mem_menu():
        sample = mem_sample.prev
        for name, val in sample._asdict().items():
            if name == "percent":
                name, val = "unavailable", sample.total - sample.available
            yield f"{menu_bytes(val)} {format_name(name)}"

    def disk_menu():
        for name, val in disk_sample.prev._asdict().items():
            if name.endswith("bytes"):
                yield f"{menu_bytes(val)}/s {format_name(name)}"
            elif name.endswith("time"):
                yield f"{val/1000:8.1%} {format_name(name)}"
            elif name.endswith("count"):
                yield f"{val:6.1f}/s {format_name(name)}"

    def net_menu():
        for name, val in net_sample.prev._asdict().items():
            if name.startswith("bytes"):
                yield f"{menu_bytes(val)}/s {format_name(name)}"
            else:
                yield f"{val:6.1f}/s {format_name(name)}"

    cpu = Sampler(
        1000, 32, cpu_sample, lambda s: normalize([s.system, s.user, s.idle])
    )
    mem = Sampler(
        1000, 32, mem_sample, lambda s: (s.total - s.available, s.available)
    )
    disk = Sampler(
        1000, 32, disk_sample, lambda s: (s.write_bytes, s.read_bytes)
    )
    net = Sampler(1000, 32, net_sample, lambda s: (s.bytes_sent, s.bytes_recv))

    disk_w = Index(disk, 0)
    disk_r = Index(disk, 1)
    net_ul = Index(net, 0)
    net_dl = Index(net, 1)

    text_format = {
        "font": "monospace",
        "size": 10,
        "color": 0xFFFFFFFF,
    }
    text_rate = text_format | {
        "formatter": lambda sample: format_bytes(sample)[0],
        "flags": Qt.AlignRight | Qt.AlignVCenter,
    }
    text_units = text_format | {
        "formatter": lambda sample: format_bytes(sample)[1] + "/s",
        "flags": Qt.AlignLeft | Qt.AlignVCenter,
    }

    def icon(*args, **kwargs):
        return TrayIcon(32, 32, *args, **kwargs)

    def graph_one(sampler, color):
        return Graph(List(sampler), [color])

    tray_icons = [
        icon(Graph(cpu, [0xFF0000FF, 0xFF00FFFF, 0x00000000]), cpu_menu),
        icon(Graph(mem, [0xFF00FF00, 0x00000000]), mem_menu),
        icon(
            VSplit(
                graph_one(disk_w, 0xFFFF0000),
                graph_one(disk_r, 0xFF00FF00),
            ),
            disk_menu,
        ),
        icon(
            VSplit(Text(disk_w, **text_rate), Text(disk_r, **text_rate)),
            disk_menu,
        ),
        icon(
            VSplit(Text(disk_w, **text_units), Text(disk_r, **text_units)),
            disk_menu,
        ),
        icon(
            VSplit(
                graph_one(net_ul, 0xFFFF0000),
                graph_one(net_dl, 0xFF00FF00),
            ),
            net_menu,
        ),
        icon(
            VSplit(Text(net_ul, **text_rate), Text(net_dl, **text_rate)),
            net_menu,
        ),
        icon(
            VSplit(
                Text(net_ul, **text_units),
                Text(net_dl, **text_units),
            ),
            net_menu,
        ),
    ]

    sys.exit(app.exec_())
