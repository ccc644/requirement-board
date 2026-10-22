"""只读探针：打印生产库与目标库的表/行数，供界面改版验证前摸底。

仅 SELECT，不做任何 DDL/DML。
用法： ``mvp\\.venv\\Scripts\\python.exe mvp/tools/_db_probe.py [dbname]``
"""
from __future__ import annotations
