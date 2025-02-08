#!/usr/bin/env python3

import itertools
import sys
import time

import psutil
from PyQt6.QtCore import QPointF, Qt, QTimer
from PyQt6.QtGui import (
    QAction,
    QColor,
    QColorConstants,
    QFont,
    QIcon,
    QPainter,
    QPixmap,
    QPolygonF,
    QTransform,
)
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon


def format_bytes(bytes):
    for prefix in ("", "K", "M", "G", "T", "P", "E", "Z", "Y"):
        if bytes < 1000:
            break
        bytes /= 1000
    return f"{bytes:4.3g}", f"{prefix}B"  # type: ignore


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


class TestPattern:
    def paint(self, painter, width, height):
        painter.save()
        for x in range(width):
            for y in range(height):
                if (x // 4 + y // 4) % 2:
                    painter.setPen(QColorConstants.White)
                else:
                    painter.setPen(QColorConstants.Black)
                painter.drawPoint(x, y)
        painter.restore()


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
        self.timer.setTimerType(Qt.TimerType.CoarseTimer)
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
        painter.setTransform(QTransform().translate(0, height // 2), combine=True)
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
    def __init__(self, width, height, painter, menuitems, tooltip):
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
        self.tray.setToolTip(tooltip)

        self.update()
        self.tray.show()

    def update(self):
        self.pixmap.fill(QColorConstants.Transparent)
        with QPainter(self.pixmap) as painter:
            painter.setRenderHints(QPainter.RenderHint.Antialiasing)
            self.painter.paint(painter, self.width, self.height)
        self.tray.setIcon(QIcon(self.pixmap))

        for text, action in zip(self.menuitems(), self.right_menu.actions()):
            action.setText(text)
            action.setEnabled(False)


app = QApplication(sys.argv)

SIZE = (48, 48)
SAMPLES = 48

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
        yield f"{val / n:6.1%} {menu_name(name)}"


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
            yield f"{val / 1000:8.1%} {menu_name(name)}"
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
    def max_sample():
        return max(val[0] for (_, val) in samples())

    return Text(lambda: formatter(max_sample()), **kwargs)


cpu_graph = graph(cpu, lambda s: normalize([s.system, s.user, s.idle]))
mem_graph = graph(mem, lambda s: (s.total - s.available, s.available))
disk_w = graph(disk, lambda s: (s.write_bytes,))
disk_r = graph(disk, lambda s: (s.read_bytes,))
net_ul = graph(net, lambda s: (s.bytes_sent,))
net_dl = graph(net, lambda s: (s.bytes_recv,))

text_format = {
    "font": "monospace",
    "size": 15,
    "color": 0xFFABB2BF,
}
text_rate = text_format | {
    "formatter": lambda sample: format_bytes(sample)[0],
    "flags": Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
}
text_units = text_format | {
    "formatter": lambda sample: format_bytes(sample)[1] + "/s",
    "flags": Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
}
symbol_format = {
    "font": "monospace",
    "size": 18,
    "color": 0x60FFFFFF,
    "flags": Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
}

cpu_icon = TrayIcon(
    *SIZE,
    Overlay(
        Text(lambda: "", **symbol_format),
        Graph(cpu_graph, [0xFFC678DD, 0xFF61AFEF, 0x00000000]),
    ),
    cpu_menu,
    "CPU",
)
mem_icon = TrayIcon(
    *SIZE,
    Overlay(
        Text(lambda: "", **symbol_format),
        Graph(mem_graph, [0xFF98C379, 0x00000000]),
    ),
    mem_menu,
    "Memory",
)
disk_icon = TrayIcon(
    *SIZE,
    Overlay(
        Text(lambda: "", **symbol_format),
        VSplit(
            Graph(disk_r, [0xFFE06C75]),
            Graph(disk_w, [0xFFE5C07B]),
        ),
    ),
    disk_menu,
    "Disk",
)
disk_rate = TrayIcon(
    *SIZE,
    VSplit(sampled_text(disk_r, **text_rate), sampled_text(disk_w, **text_rate)),
    disk_menu,
    "Disk",
)
disk_units = TrayIcon(
    *SIZE,
    VSplit(sampled_text(disk_r, **text_units), sampled_text(disk_w, **text_units)),
    disk_menu,
    "Disk",
)
net_icon = TrayIcon(
    *SIZE,
    Overlay(
        Text(lambda: "", **symbol_format),
        VSplit(
            Graph(net_ul, [0xFFE06C75]),
            Graph(net_dl, [0xFFE5C07B]),
        ),
    ),
    net_menu,
    "Network",
)
net_rate = TrayIcon(
    *SIZE,
    VSplit(sampled_text(net_ul, **text_rate), sampled_text(net_dl, **text_rate)),
    net_menu,
    "Network",
)
net_units = TrayIcon(
    *SIZE,
    VSplit(
        sampled_text(net_ul, **text_units),
        sampled_text(net_dl, **text_units),
    ),
    net_menu,
    "Network",
)

samplers = [
    Sampler(1000, cpu, [cpu_icon]),
    Sampler(3000, mem, [mem_icon]),
    Sampler(2000, disk, [disk_icon, disk_rate, disk_units]),
    Sampler(2000, net, [net_icon, net_rate, net_units]),
]


def main():
    for _ in range(50):
        if QSystemTrayIcon.isSystemTrayAvailable():
            break
        time.sleep(0.1)
    else:
        sys.exit(1)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
