#!/usr/bin/env python3
"""根据 data/editions 与 data/reports 重建 data/index.json。"""

import lib


def main() -> int:
    index = lib.rebuild_index()
    editions = index.get("editions", [])
    reports = index.get("reports", [])
    print(f"索引已更新：{len(editions)} 期周报，{len(reports)} 份月报")
    if editions:
        print(f"最新一期：{editions[0]['date']} · {editions[0]['title']}")
        print(f"下次更新：{index['site'].get('nextUpdate')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
