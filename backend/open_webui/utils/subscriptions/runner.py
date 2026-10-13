"""Old entry point for the Runner, which now lives in the buddy_runner package.

Launchers written before the move run ``python -m open_webui.utils.subscriptions.runner``;
new ones run ``python -m buddy_runner``.
"""

from buddy_runner.runner import main

if __name__ == '__main__':
    main()
