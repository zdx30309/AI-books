"""
文本工具 - 中文友好的字数统计

count_words: 5000 字章节墙的判定基准
  - CJK 汉字逐字计数
  - 英文 / 数字按 token (连续字母数字串) 计数
  - 标点 / 空白不计入
"""
import re

_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")
_ALNUM_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def count_words(text: str) -> int:
    """返回中文'字数'。空字符串返回 0。"""
    if not text:
        return 0
    return len(_CJK_RE.findall(text)) + len(_ALNUM_TOKEN_RE.findall(text))
