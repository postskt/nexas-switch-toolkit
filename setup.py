# -*- coding: utf-8 -*-
from setuptools import setup, find_packages

setup(
    name="nexas-switch-toolkit",
    version="1.2.0",
    packages=find_packages(),
    install_requires=[
        "zstandard>=0.22.0",
        "pillow>=10.0.0",
    ],
    entry_points={
        "console_scripts": [
            "nexas-tool=nexas_toolkit.cli:main",
        ],
    },
)
