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


def timestamp(it):
    for x in it:
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
        self.timer.timeout.connect(self.timeout)
        self.timer.start(interval)

    def timeout(self):
        self.data_source.push()

        for icon in self.icons:
            icon.draw()


class Graph:
    def __init__(self, samples, colors):
        self.samples = samples
        self.colors = colors

    def __call__(self, painter, width, height):
        samples = list(self.samples())
        samples.reverse()
        total = max(sum(sample[1]) for sample in samples)
        if total == 0:
            return
        scale = 1 / total

        left = samples[0][0]
        right = samples[-1][0]

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
    def __init__(self, samples, formatter, font, size, color, flags):
        self.samples = samples
        self.formatter = formatter
        self.font = font
        self.size = size
        self.color = color
        self.flags = flags

    def __call__(self, painter, width, height):
        text = self.formatter(next(self.samples())[1][0])
        painter.setPen(QColor.fromRgba(self.color))
        painter.setFont(QFont(self.font, self.size))
        painter.drawText(0, 0, width, height, self.flags, text)


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

    def timestamp_iter(fn):
        return timestamp(iter(fn, None))

    cpu = DataSource(32, rate(timestamp_iter(psutil.cpu_times)))
    mem = DataSource(32, timestamp_iter(psutil.virtual_memory))
    disk = DataSource(32, rate(timestamp_iter(psutil.disk_io_counters)))
    net = DataSource(32, rate(timestamp_iter(psutil.net_io_counters)))

    def format_name(name):
        return name.title().replace("_", " ")

    def menu_bytes(val):
        bytes, units = format_bytes(val)
        if len(units) == 1:
            bytes = " " + bytes
        return f"{bytes}{units}"

    def last(sampler):
        return next(iter(sampler.window))[1]

    def cpu_menu():
        n = psutil.cpu_count()
        for name, val in last(cpu)._asdict().items():
            yield f"{val/n:6.1%} {format_name(name)}"

    def mem_menu():
        sample = last(mem)
        for name, val in sample._asdict().items():
            if name == "percent":
                name, val = "unavailable", sample.total - sample.available
            yield f"{menu_bytes(val)} {format_name(name)}"

    def disk_menu():
        for name, val in last(disk)._asdict().items():
            if name.endswith("bytes"):
                yield f"{menu_bytes(val)}/s {format_name(name)}"
            elif name.endswith("time"):
                yield f"{val/1000:8.1%} {format_name(name)}"
            elif name.endswith("count"):
                yield f"{val:6.1f}/s {format_name(name)}"

    def net_menu():
        for name, val in last(net)._asdict().items():
            if name.startswith("bytes"):
                yield f"{menu_bytes(val)}/s {format_name(name)}"
            else:
                yield f"{val:6.1f}/s {format_name(name)}"

    def cpu_graph():
        for ts, s in cpu.window:
            yield ts, normalize([s.system, s.user, s.idle])

    def mem_graph():
        for ts, s in mem.window:
            yield ts, (s.total - s.available, s.available)

    def disk_w():
        for ts, s in disk.window:
            yield ts, (s.write_bytes,)

    def disk_r():
        for ts, s in disk.window:
            yield ts, (s.read_bytes,)

    def net_ul():
        for ts, s in net.window:
            yield ts, (s.bytes_sent,)

    def net_dl():
        for ts, s in net.window:
            yield ts, (s.bytes_recv,)

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

    cpu_icon = icon(
        Graph(cpu_graph, [0xFF0000FF, 0xFF00FFFF, 0x00000000]), cpu_menu
    )

    mem_icon = icon(Graph(mem_graph, [0xFF00FF00, 0x00000000]), mem_menu)
    disk_icon = icon(
        VSplit(
            Graph(disk_w, [0xFFFF0000]),
            Graph(disk_r, [0xFF00FF00]),
        ),
        disk_menu,
    )
    disk_rate = icon(
        VSplit(Text(disk_w, **text_rate), Text(disk_r, **text_rate)),
        disk_menu,
    )
    disk_units = icon(
        VSplit(Text(disk_w, **text_units), Text(disk_r, **text_units)),
        disk_menu,
    )
    net_icon = icon(
        VSplit(
            Graph(net_ul, [0xFFFF0000]),
            Graph(net_dl, [0xFF00FF00]),
        ),
        net_menu,
    )
    net_rate = icon(
        VSplit(Text(net_ul, **text_rate), Text(net_dl, **text_rate)),
        net_menu,
    )
    net_units = icon(
        VSplit(
            Text(net_ul, **text_units),
            Text(net_dl, **text_units),
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
