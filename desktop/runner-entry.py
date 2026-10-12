"""Entry point PyInstaller freezes into buddy-runner.exe; the same as ``python -m buddy_runner``."""

from buddy_runner.runner import main

if __name__ == '__main__':
    main()
