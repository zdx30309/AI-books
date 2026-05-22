from __future__ import annotations

import argparse
from pathlib import Path
from urllib.request import urlretrieve


SOURCE_URL = "https://github.com/g0v/moedict-data/raw/refs/heads/master/dict-revised.json.xz"
NOTICE = """# 古文白话本地词典资源

当前文件：`dict-revised.json.xz`

来源：g0v/moedict-data 的 `dict-revised.json.xz`，内容来自教育部《重编国语辞典修订本》。

授权：教育部国语辞典公众授权资料采用「创用 CC 姓名标示 禁止改作 3.0 台湾授权条款」释出。代码只把原始资料作为本地查询参考，不改写词典内容。

用途：古文白话任务会按输入原文抽取相关词条，作为 prompt 的词义参考上下文，避免纯靠模型猜古义。
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Download the local Classical Chinese dictionary data.")
    parser.add_argument(
        "--out",
        default="backend/output/classical_dictionary/dict-revised.json.xz",
        help="Output path for the compressed dictionary JSON.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing dictionary file.")
    args = parser.parse_args()

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not args.force:
        print(f"exists: {target}")
    else:
        print(f"downloading: {SOURCE_URL}")
        urlretrieve(SOURCE_URL, target)
        print(f"saved: {target} ({target.stat().st_size:,} bytes)")

    notice_path = target.parent / "README.md"
    notice_path.write_text(NOTICE, encoding="utf-8")
    print(f"notice: {notice_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
