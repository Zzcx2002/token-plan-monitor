"""Standalone macOS bundle. Run through scripts/build_macos.py."""
from setuptools import setup
from py2app.build_app import py2app

from token_plan_monitor import __version__


class StandaloneBundle(py2app):
    def finalize_options(self):
        # Dependencies are preinstalled by pip. py2app rejects the install_requires
        # metadata setuptools reads from pyproject.toml; it uses modulegraph instead.
        self.distribution.install_requires = []
        super().finalize_options()

    def collect_recipedict(self):
        recipes = super().collect_recipedict()
        # 0.28.10's Tk recipe mistakes an ExcludedModule for a real import and
        # starts Tk during packaging. This native shell deliberately has no Tk.
        recipes.pop("tkinter", None)
        return recipes

setup(
    cmdclass={"py2app": StandaloneBundle},
    app=["scripts/macos_launcher.py"],
    options={"py2app": {
        "argv_emulation": False,
        "arch": "arm64",
        "iconfile": "build/assets/app.icns",
        "resources": ["LICENSE", "THIRD_PARTY_NOTICES.md", "licenses"],
        "packages": ["token_plan_monitor", "requests", "certifi", "keyring"],
        "includes": ["keyring.backends.macOS", "AppKit", "Foundation", "Quartz", "objc",
                     "UserNotifications"],
        # setuptools/pkg_resources/_distutils_hack are build-time only: nothing
        # in __boot__.py or the app imports them, and keyring 25.x discovers
        # backends via importlib.metadata (its dist-info ships independently).
        # "test" is the stdlib test package -- 374 dead entries in the zip.
        # NOTE: the Tk shell modules (token_plan_monitor.ui / ui_graphics /
        # desktop_widget) are NOT listed here: `packages` copies the whole
        # package directory, so excludes cannot stop them (~96KB, never
        # imported on macOS). Listing them only faked an exclusion.
        "excludes": ["tkinter", "_tkinter", "pystray", "winreg", "pytest",
                     "setuptools", "pkg_resources", "_distutils_hack", "test"],
        "plist": {
            "CFBundleName": "TokenPlanMonitor",
            "CFBundleDisplayName": "Token Plan 用量监控",
            "CFBundleIdentifier": "com.zzcx2002.tokenplanmonitor",
            # Same source as the dmg name (scripts/build_macos.py) and
            # pyproject's dynamic version: __init__.py is the single truth.
            "CFBundleShortVersionString": __version__,
            # The one hand-maintained build number: increment on every
            # published build, even when the marketing version stays put.
            "CFBundleVersion": "6",
            "NSHumanReadableCopyright": "Copyright © 2026 Zzcx2002",
            "LSUIElement": True,
            "LSMinimumSystemVersion": "14.0",
            "NSHighResolutionCapable": True,
        },
    }},
)
