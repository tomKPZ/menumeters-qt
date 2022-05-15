#!/usr/bin/env python3

import itertools
import sys
import time

import psutil
from PyQt5.QtCore import QPointF, Qt, QTimer
from PyQt5.QtGui import (QColor, QColorConstants, QFont, QIcon, QPainter,
                         QPixmap, QPolygonF, QTransform)
from PyQt5.QtWidgets import QAction, QApplication, QMenu, QSystemTrayIcon


def format_bytes(bytes):
    for prefix in ("", "K", "M", "G", "T", "P", "E", "Z", "Y"):
        if bytes < 1000:
            break
        bytes /= 1000
    return f"{bytes:4.3g}", f"{prefix}B"


def lerp(x, in_min, in_max, out_min, out_max):
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min


def normalize(sample):
    total = sum(sample)
    if total == 0:
        return [0] * len(sample)
    return [x / total for x in sample]


def timestamp(fn):
    for x in iter(fn, None):
        yield time.monotonic(), x


def rate(it):
    pt, px = next(it)
    for t, x in it:
        yield t, type(x)._make((s2 - s1) / (t - pt) for (s1, s2) in zip(px, x))
        pt, px = t, x


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


class DataSource:
    def __init__(self, window, source):
        self.window = SlidingWindow(window)
        self.source = source

        # Prime with 2 samples for graph rendering.
        for _ in range(2):
            self.push()

    def push(self):
        self.window.push(next(self.source))


class Sampler:
    def __init__(self, interval, data_source, icons):
        self.data_source = data_source
        self.icons = icons

        self.timer = QTimer()
        self.timer.setTimerType(Qt.CoarseTimer)
        self.timer.timeout.connect(self.timeout)
        self.timer.start(interval)

    def timeout(self):
        self.data_source.push()

        for icon in self.icons:
            icon.update()


class Graph:
    def __init__(self, samples, colors):
        self.samples = samples
        self.colors = colors

    def paint(self, painter, width, height):
        samples = list(self.samples())
        total = max(sum(sample[1]) for sample in samples)
        if total == 0:
            return
        scale = 1 / total

        left = samples[-1][0]
        right = samples[0][0]

        series = [[] for _ in range(1 + len(self.colors))]
        for ts, sample in self.samples():
            x = lerp(ts, left, right, 0, width)
            y = height
            for val, row in zip(sample, series):
                row.append(QPointF(x, y))
                y -= val * scale * height
            series[-1].append(QPointF(x, y))

        painter.save()
        painter.setPen(QColorConstants.Transparent)
        painter.setCompositionMode(QPainter.CompositionMode_Plus)
        for color, (lo, hi) in zip(self.colors, itertools.pairwise(series)):
            painter.setBrush(QColor.fromRgba(color))
            painter.drawPolygon(QPolygonF(lo + list(reversed(hi))))
        painter.restore()


class Text:
    def __init__(self, text, font, size, color, flags):
        self.text = text
        self.font = font
        self.size = size
        self.color = color
        self.flags = flags

    def paint(self, painter, width, height):
        text = self.text()
        painter.setPen(QColor.fromRgba(self.color))
        painter.setFont(QFont(self.font, self.size))
        painter.drawText(0, 0, width, height, self.flags, text)


class VSplit:
    def __init__(self, top, bottom):
        self.top = top
        self.bottom = bottom

    def paint(self, painter, width, height):
        self.top.paint(painter, width, height // 2)
        painter.save()
        painter.setTransform(
            QTransform().translate(0, height // 2), combine=True
        )
        self.bottom.paint(painter, width, height // 2)
        painter.restore()


class Overlay:
    def __init__(self, top, bottom):
        self.top = top
        self.bottom = bottom

    def paint(self, painter, width, height):
        self.bottom.paint(painter, width, height)
        self.top.paint(painter, width, height)


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

        self.update()
        self.tray.show()

    def update(self):
        self.pixmap.fill(QColorConstants.Transparent)
        with QPainter(self.pixmap) as painter:
            painter.setRenderHints(QPainter.Antialiasing)
            self.painter.paint(painter, self.width, self.height)
        self.tray.setIcon(QIcon(self.pixmap))

        for text, action in zip(self.menuitems(), self.right_menu.actions()):
            action.setText(text)
            action.setEnabled(False)


app = QApplication(sys.argv)

SIZE = (32, 32)
SAMPLES = 32

cpu = DataSource(SAMPLES, rate(timestamp(psutil.cpu_times)))
mem = DataSource(SAMPLES, timestamp(psutil.virtual_memory))
disk = DataSource(SAMPLES, rate(timestamp(psutil.disk_io_counters)))
net = DataSource(SAMPLES, rate(timestamp(psutil.net_io_counters)))


def menu_name(name):
    return name.title().replace("_", " ")


def menu_bytes(val):
    bytes, units = format_bytes(val)
    if len(units) == 1:
        bytes = " " + bytes
    return f"{bytes}{units}"


def menu_data(sampler):
    return next(iter(sampler.window))[1]


def cpu_menu():
    n = psutil.cpu_count()
    for name, val in menu_data(cpu)._asdict().items():
        yield f"{val/n:6.1%} {menu_name(name)}"


def mem_menu():
    sample = menu_data(mem)
    for name, val in sample._asdict().items():
        if name == "percent":
            name, val = "unavailable", sample.total - sample.available
        yield f"{menu_bytes(val)} {menu_name(name)}"


def disk_menu():
    for name, val in menu_data(disk)._asdict().items():
        if name.endswith("bytes"):
            yield f"{menu_bytes(val)}/s {menu_name(name)}"
        elif name.endswith("time"):
            yield f"{val/1000:8.1%} {menu_name(name)}"
        elif name.endswith("count"):
            yield f"{val:6.1f}/s {menu_name(name)}"


def net_menu():
    for name, val in menu_data(net)._asdict().items():
        if name.startswith("bytes"):
            yield f"{menu_bytes(val)}/s {menu_name(name)}"
        else:
            yield f"{val:6.1f}/s {menu_name(name)}"


def graph(source, mapper):
    def impl():
        for ts, s in source.window:
            yield ts, mapper(s)

    return impl


def sampled_text(samples, formatter, **kwargs):
    return Text(lambda: formatter(next(samples())[1][0]), **kwargs)


cpu_graph = graph(cpu, lambda s: normalize([s.system, s.user, s.idle]))
mem_graph = graph(mem, lambda s: (s.total - s.available, s.available))
disk_w = graph(disk, lambda s: (s.write_bytes,))
disk_r = graph(disk, lambda s: (s.read_bytes,))
net_ul = graph(net, lambda s: (s.bytes_sent,))
net_dl = graph(net, lambda s: (s.bytes_recv,))

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
symbol_format = {
    "font": "monospace",
    "size": 12,
    "color": 0x60FFFFFF,
    "flags": Qt.AlignLeft | Qt.AlignTop,
}

cpu_icon = TrayIcon(
    *SIZE,
    Overlay(
        Graph(cpu_graph, [0xFF0000FF, 0xFF00FFFF, 0x00000000]),
        Text(lambda: "", **symbol_format),
    ),
    cpu_menu,
)
mem_icon = TrayIcon(
    *SIZE,
    Overlay(
        Graph(mem_graph, [0xFF00FF00, 0x00000000]),
        Text(lambda: "", **symbol_format),
    ),
    mem_menu,
)
disk_icon = TrayIcon(
    *SIZE,
    Overlay(
        VSplit(
            Graph(disk_w, [0xFFFF0000]),
            Graph(disk_r, [0xFF00FF00]),
        ),
        Text(lambda: "", **symbol_format),
    ),
    disk_menu,
)
disk_rate = TrayIcon(
    *SIZE,
    VSplit(
        sampled_text(disk_w, **text_rate), sampled_text(disk_r, **text_rate)
    ),
    disk_menu,
)
disk_units = TrayIcon(
    *SIZE,
    VSplit(
        sampled_text(disk_w, **text_units), sampled_text(disk_r, **text_units)
    ),
    disk_menu,
)
net_icon = TrayIcon(
    *SIZE,
    Overlay(
        VSplit(
            Graph(net_ul, [0xFFFF0000]),
            Graph(net_dl, [0xFF00FF00]),
        ),
        Text(lambda: "", **symbol_format),
    ),
    net_menu,
)
net_rate = TrayIcon(
    *SIZE,
    VSplit(
        sampled_text(net_ul, **text_rate), sampled_text(net_dl, **text_rate)
    ),
    net_menu,
)
net_units = TrayIcon(
    *SIZE,
    VSplit(
        sampled_text(net_ul, **text_units),
        sampled_text(net_dl, **text_units),
    ),
    net_menu,
)

samplers = [
    Sampler(100, cpu, [cpu_icon]),
    Sampler(100, mem, [mem_icon]),
    Sampler(100, disk, [disk_icon, disk_rate, disk_units]),
    Sampler(100, net, [net_icon, net_rate, net_units]),
]

sys.exit(app.exec_())
