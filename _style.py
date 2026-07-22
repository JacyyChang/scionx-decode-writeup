# -*- coding: utf-8 -*-
"""
_style.py -- shared plotting setup for the diagnostic scripts in this folder.

Responsibility: set up a consistent matplotlib backend/font/color palette so
each script only has to worry about drawing its own content.
"""

import os

OUT_DIR = os.getcwd()

# color palette (matches the MATLAB standard color order)
BLUE = (0.0000, 0.4470, 0.7410)
ORANGE = (0.8500, 0.3250, 0.0980)
YELLOW = (0.9290, 0.6940, 0.1250)
PURPLE = (0.4940, 0.1840, 0.5560)
GREEN = (0.4660, 0.6740, 0.1880)
GRAY = (0.5000, 0.5000, 0.5000)


def setup_mpl():
    """Set up the backend and figure defaults, and return the pyplot module.
    No GUI (Agg backend) -- always saves to PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["figure.dpi"] = 110
    plt.rcParams["savefig.dpi"] = 130
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.3
    return plt


def save(fig, name):
    """Save to <cwd>/<name> and return the full path. name may include a
    subfolder (e.g. "Figure/xxx.png"); the subfolder is created automatically
    if it doesn't exist."""
    path = os.path.join(OUT_DIR, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.tight_layout()
    fig.savefig(path)
    return path
