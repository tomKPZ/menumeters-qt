#!/usr/bin/env python3

import setuptools

setuptools.setup(
    name="menumeters-qt",
    version="0.1.0",
    description="A port of macOS MenuMeters to Qt",
    author="tomKPZ",
    license="GPLv3",
    python_requires=">=3.10",
    install_requires=[
        "psutil",
        "PyQt6",
    ],
    entry_points={
        "console_scripts": [
            "menumeters-qt=menumeters_qt:main",
        ],
    },
)
