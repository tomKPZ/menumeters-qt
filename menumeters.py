#!/usr/bin/env python3

from PyQt5.QtCore import QTimer, QLineF
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction
from PyQt5.QtGui import QIcon, QPainter, QPixmap, QColorConstants
import psutil
import sys


def mem_percent():
    mem = psutil.virtual_memory()
    return (mem.total - mem.available) / mem.total


class TrayIcon(QSystemTrayIcon):

    def __init__(self, parent, interval, color, get_usage):
        QSystemTrayIcon.__init__(self, parent)

        self.get_usage = get_usage
        self.color = color
        self.usage = [0] * 32
        self.draw()

        right_menu = RightClicked()
        self.setContextMenu(right_menu)

        self.show()

        self.timer = QTimer()
        self.timer.timeout.connect(self.timeout)
        self.timer.start(interval)

    def timeout(self):
        usage = self.get_usage()
        self.usage = self.usage[1:] + [usage]
        self.draw()

    def draw(self):
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColorConstants.Transparent)
        with QPainter(pixmap) as painter:
            painter.setPen(self.color)
            painter.setRenderHints(QPainter.Antialiasing)
            for i, usage in enumerate(self.usage):
                painter.drawLine(QLineF(i, 32 * (1 - usage), i, 32))
        icon = QIcon(pixmap)
        self.setIcon(icon)


class RightClicked(QMenu):

    def __init__(self, parent=None):
        QMenu.__init__(self, parent=None)

        action = QAction("Exit", self)
        action.triggered.connect(lambda: QApplication.exit(0))
        self.addAction(action)


if __name__ == "__main__":
    app = QApplication(sys.argv)

    cpu = TrayIcon(app, 100, QColorConstants.Cyan,
                   lambda: psutil.cpu_percent() / 100)
    mem = TrayIcon(app, 100, QColorConstants.Green, mem_percent)

    sys.exit(app.exec_())
