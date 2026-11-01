"""`python -m livetv` 入口，等价于 `livetv` 控制台命令。"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
