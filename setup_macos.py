"""Standalone macOS bundle. Run through scripts/build_macos.py."""
from setuptools import setup
from py2app.build_app import py2app


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
        "includes": ["keyring.backends.macOS", "AppKit", "Foundation", "Quartz", "objc"],
        "excludes": ["tkinter", "_tkinter", "pystray", "winreg", "pytest",
                     "token_plan_monitor.ui", "token_plan_monitor.ui_graphics", "token_plan_monitor.desktop_widget"],
        "plist": {
            "CFBundleName": "TokenPlanMonitor",
            "CFBundleDisplayName": "火山方舟用量监控",
            "CFBundleIdentifier": "com.zzcx2002.tokenplanmonitor",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "1",
            "NSHumanReadableCopyright": "Copyright © 2026 Zzcx2002",
            "LSUIElement": True,
            "LSMinimumSystemVersion": "14.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": True,
        },
    }},
)
