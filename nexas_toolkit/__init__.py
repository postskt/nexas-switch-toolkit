# -*- coding: utf-8 -*-
"""
nexas-switch-toolkit: Universal NeXAS Engine Cross-Platform Porter (PC -> Switch)
"""

from .pipeline import PipelineConfig, PipelineRunner
from .gui import start_gui_server

__version__ = "2.0.0"
__author__ = "NeXAS Switch Porting Team"

__all__ = [
    "PipelineConfig",
    "PipelineRunner",
    "start_gui_server",
]
