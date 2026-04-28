from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Pattern

SENTENCE_BREAKS = "。！？；\n"


class RuleCategory(Enum):
    FIXED_STRING = "fixed_string"
    SIMPLE_PATTERN = "simple_pattern"
    TEMPLATE_BLANK = "template_blank"
    PHRASE = "phrase"
    CROSS_SENTENCE = "cross_sentence"


@dataclass(frozen=True)
class BannedRule:
    rule_id: str
    category: RuleCategory
    name: str
    detector_regex: Pattern[str]
    forbidden_substrings: tuple[str, ...]
    rewrite_instruction: str
    severity: int = 5


def _make_fixed_detector(s: str) -> Pattern[str]:
    """匹配包含 s 的整个句子（句号/感叹号/问号/分号/换行为边界）。"""
    return re.compile(rf"[^。！？；\n]*{re.escape(s)}[^。！？；\n]*[。！？；\n]?")


def _make_pattern_detector(pattern: str) -> Pattern[str]:
    return re.compile(rf"[^。！？；\n]*{pattern}[^。！？；\n]*[。！？；\n]?")


RULES: tuple[BannedRule, ...] = (
    BannedRule(
        "A1_01_buShi_erShi",
        RuleCategory.SIMPLE_PATTERN,
        "不是...而是",
        _make_pattern_detector(r"不是[^。！？；\n]{1,30}而是"),
        ("不是", "而是"),
        "把句子拆成两个独立陈述句。\n\n写法要求：\n- 先给出一个完整判断，再给出另一个完整判断。\n- 绝对不要在同一句里同时出现「不是」和「而是」。\n- 例如：X 不是 A，而是 B。应改成：X 是 B。它不是 A 那种情况。",
        10,
    ),
    BannedRule(
        "A1_25_xiangShiZai_gengXiangShi",
        RuleCategory.SIMPLE_PATTERN,
        "像是在...更像是...",
        _make_pattern_detector(r"像是在[^。！？；\n]{1,30}更像是"),
        ("像是在", "更像是"),
        "删除「像是在」这一层铺垫，只保留你真正要表达的判断。两层比喻都不必要时，直接说实质。",
        9,
    ),
    BannedRule(
        "A2_yuQiShuo_buRuShuo",
        RuleCategory.SIMPLE_PATTERN,
        "与其说...不如说...",
        _make_pattern_detector(r"与其说[^。！？；\n]{1,30}不如说"),
        ("与其说", "不如说"),
        "直接陈述你认为它是什么，不要用比较句式兜圈子。把整句改成一个明确判断句。",
        8,
    ),
    BannedRule(
        "A2_kanShi_shiZe",
        RuleCategory.SIMPLE_PATTERN,
        "看似...实则...",
        _make_pattern_detector(r"看似[^。！？；\n]{1,30}实则"),
        ("看似", "实则"),
        "拆成两句：先客观描述表象，再独立陈述实质。不要把表象和结论捆在同一句里。",
        10,
    ),
    BannedRule(
        "A2_jiShi_yeShi",
        RuleCategory.SIMPLE_PATTERN,
        "既是...也是...",
        _make_pattern_detector(r"既是[^。！？；\n]{1,20}也是"),
        ("既是", "也是"),
        "拆成两句分别陈述两个属性。避免在一句里同时使用「既是……也是……」。",
        7,
    ),
    BannedRule(
        "A2_biaoMianShang_benZhiShang",
        RuleCategory.SIMPLE_PATTERN,
        "表面上...本质上...",
        _make_pattern_detector(r"表面上[^。！？；\n]{1,20}本质上"),
        ("表面上", "本质上"),
        "把表象和实质拆开写，不要用对照句式一次性打包。",
        9,
    ),
    BannedRule(
        "A1_14_tuoZhu",
        RuleCategory.FIXED_STRING,
        "托住",
        _make_fixed_detector("托住"),
        ("托住",),
        "整句重写，不许出现「托住」二字。根据上下文选更具体的动词，如支撑、承担、兜住、扛住、撑起、维持等，不要简单同义替换。",
        8,
    ),
    BannedRule(
        "A1_22_ningDaoYiQi",
        RuleCategory.FIXED_STRING,
        "拧到了一起",
        _make_fixed_detector("拧到了一起"),
        ("拧到了一起",),
        "重写句子，禁用「拧到了一起」。可改为绑在一起、紧密相关、同时出现、彼此纠缠等更自然的表达。",
        6,
    ),
    BannedRule(
        "A1_11_yeJiuShiShuo",
        RuleCategory.FIXED_STRING,
        "也就是说，",
        _make_fixed_detector("也就是说，"),
        ("也就是说",),
        "删除连接词，让前后两句各自独立成立。也可以改为换句话说或这意味着，但必须同步处理标点，去掉那个全角逗号。",
        7,
    ),
    BannedRule(
        "A1_20_baHuaShuoTou",
        RuleCategory.FIXED_STRING,
        "把话说透",
        _make_fixed_detector("把话说透"),
        ("把话说透",),
        "不要先说「把话说透」之类的元语言，直接把判断本身说出来。删掉铺垫，改成明确陈述。",
        6,
    ),
    BannedRule(
        "A1_21_baHuaZhiBai",
        RuleCategory.FIXED_STRING,
        "把话说得再直白一点",
        _make_fixed_detector("把话说得再直白一点"),
        ("把话说得再直白",),
        "删除元语言铺垫，直接给出结论或判断。不要再用「把话说得再直白一点」来提示读者。",
        6,
    ),
    BannedRule(
        "A1_29_zhiBaiYiDian",
        RuleCategory.FIXED_STRING,
        "直白一点讲",
        _make_fixed_detector("直白一点讲"),
        ("直白一点讲",),
        "删除这类自我提示语，直接进入核心判断。不要先声明「直白一点讲」。",
        5,
    ),
    BannedRule(
        "A1_28_wenTiGuanJian",
        RuleCategory.FIXED_STRING,
        "问题的关键",
        _make_fixed_detector("问题的关键"),
        ("问题的关键",),
        "不要写成「问题的关键是 X」的总括句，直接把关键点写出来。让句子本身承担判断。",
        7,
    ),
    BannedRule(
        "A1_24_diChuShui",
        RuleCategory.FIXED_STRING,
        "得能滴出水",
        _make_fixed_detector("得能滴出水"),
        ("得能滴出水",),
        "整句重写，避免这种夸张比喻。改用更直接的状态描述或感受描述。",
        6,
    ),
    BannedRule(
        "A1_30_baLianTiao",
        RuleCategory.FIXED_STRING,
        "把这条链条慢慢展开",
        _make_fixed_detector("把这条链条慢慢展开"),
        ("链条", "慢慢展开"),
        "删除元叙事，不要说「把这条链条慢慢展开」。直接进入事实、关系或过程本身。",
        6,
    ),
    BannedRule(
        "A1_26_muBiaoZhiJie",
        RuleCategory.FIXED_STRING,
        "我们的目标很直接",
        _make_fixed_detector("我们的目标很直接"),
        ("我们的目标",),
        "删除作者宣言式开头，直接说明要做什么、为什么做、怎么做。不要用「我们的目标很直接」。",
        5,
    ),
    BannedRule(
        "A1_08_zaiWangShenChu",
        RuleCategory.FIXED_STRING,
        "再往深处看",
        _make_fixed_detector("再往深处看"),
        ("再往深处看",),
        "不要先说「再往深处看」，直接说更深一层的判断或证据。去掉提示动作。",
        6,
    ),
    BannedRule(
        "A1_09_zhiSuoYiZhiDe",
        RuleCategory.FIXED_STRING,
        "之所以值得写",
        _make_fixed_detector("之所以值得写"),
        ("之所以值得写",),
        "删除元叙事，直接展示它为什么重要、影响在哪里、价值在哪里。不要用「之所以值得写」。",
        5,
    ),
    BannedRule(
        "A1_10_henNengShuoMing",
        RuleCategory.FIXED_STRING,
        "这个变化很能说明",
        _make_fixed_detector("这个变化很能说明"),
        ("很能说明",),
        "重写成具体说明句，直接说出它说明了什么，不要用总评式句子。",
        6,
    ),
    BannedRule(
        "A1_33_dingDeHenSi",
        RuleCategory.FIXED_STRING,
        "定得很死",
        _make_fixed_detector("定得很死"),
        ("定得很死",),
        "用具体描述替代抽象强度词，例如严格规定、硬性要求、不许变通、不能修改等。",
        5,
    ),
    BannedRule(
        "A1_35_xianShiGan",
        RuleCategory.FIXED_STRING,
        "很有现实感的",
        _make_fixed_detector("很有现实感的"),
        ("很有现实感",),
        "删除该形容词，让事实、动作或后果自己说话。不要用「很有现实感的」给句子贴标签。",
        4,
    ),
    BannedRule(
        "A1_23_feiChangShiJi",
        RuleCategory.FIXED_STRING,
        "非常实际的",
        _make_fixed_detector("非常实际的"),
        ("非常实际",),
        "删除强度副词，用具体内容说明实际在哪里。不要保留「非常实际的」这种评价词。",
        4,
    ),
    BannedRule(
        "A1_12_feiChangYing",
        RuleCategory.FIXED_STRING,
        "非常硬的",
        _make_fixed_detector("非常硬的"),
        ("非常硬",),
        "同类强度词一律改写为具体描述，避免空泛强化。",
        4,
    ),
    BannedRule(
        "A1_13_henYing",
        RuleCategory.FIXED_STRING,
        "很硬的",
        _make_fixed_detector("很硬的"),
        ("很硬",),
        "不要使用抽象强度词，改成能够落地的具体形容。",
        4,
    ),
    BannedRule(
        "A1_15_henDuoYiTiDao",
        RuleCategory.FIXED_STRING,
        "很多人一提到",
        _make_fixed_detector("很多人一提到"),
        ("很多人一提到",),
        "重写为具体场景、具体群体或明确反应，不要用「很多人一提到」来虚指。",
        5,
    ),
    BannedRule(
        "A1_07_lianQiLai",
        RuleCategory.FIXED_STRING,
        "连起来",
        _make_fixed_detector("连起来"),
        ("连起来",),
        "当它承担段落收束或串联动作时禁用。改成放在一起看、结合、综合、串联起来的关系等更具体的表达。",
        5,
    ),
    BannedRule(
        "A1_06_wangYiQiTui",
        RuleCategory.FIXED_STRING,
        "往一起推",
        _make_fixed_detector("往一起推"),
        ("往一起推",),
        "重写，禁用「往一起推」这种笼统收束动作。改用更明确的分析动作。",
        5,
    ),
    BannedRule(
        "A1_34_zaiChenXiaQu",
        RuleCategory.FIXED_STRING,
        "沉下去",
        _make_fixed_detector("沉下去"),
        ("沉下去",),
        "重写收束句，禁用「沉下去」这类抽象下沉表达。改成更具体的结束、退场或进入下一层的说法。",
        5,
    ),
    BannedRule(
        "A1_32_huiYueLaiYueShun",
        RuleCategory.FIXED_STRING,
        "会越来越顺",
        _make_fixed_detector("会越来越顺"),
        ("越来越顺",),
        "删除空泛预测，改成可观察的趋势、条件或结果。不要只说「会越来越顺」。",
        4,
    ),
    BannedRule(
        "A1_02_ruGuoZhiFenLiang",
        RuleCategory.SIMPLE_PATTERN,
        "如果只...分量就轻了",
        _make_pattern_detector(r"如果只.{1,30}分量就轻了"),
        ("分量就轻了",),
        "避免这种威胁式句型。改为直接说明后果、代价或判断，不要把句子写成「如果不 X，就 Y」的压迫感结构。",
        8,
    ),
    BannedRule(
        "A1_27_yiBuBuLaZhi",
        RuleCategory.SIMPLE_PATTERN,
        "一步步把...拉直",
        _make_pattern_detector(r"一步步把.{1,20}拉直"),
        ("一步步", "拉直"),
        "删除元叙事，不要写「一步步把……拉直」。直接写处理结果、调整方式或最终形态。",
        6,
    ),
    BannedRule(
        "A1_31_baSongGuoQu",
        RuleCategory.SIMPLE_PATTERN,
        "把...送过去",
        _make_pattern_detector(r"把.{1,15}送过去"),
        ("送过去",),
        "重写时改用具体动词，如交给、转给、抛给、递给等，避免泛化的「送过去」。",
        5,
    ),
    BannedRule(
        "A1_16_changChuLai",
        RuleCategory.TEMPLATE_BLANK,
        "长出来",
        _make_pattern_detector(r"[^。！？；\n]{1,15}长出来"),
        ("长出来",),
        "把「长出来」改成更具体的动词：出现、形成、冒头、涌现、生成。根据语境选择最准确的一个。",
        7,
    ),
    BannedRule(
        "A1_17_jieZhu",
        RuleCategory.TEMPLATE_BLANK,
        "接住",
        _make_pattern_detector(r"[^。！？；\n]{1,15}接住"),
        ("接住",),
        "把「接住」改为承接、对应、匹配、兜住、应对等更准确的动词。不要原词保留。",
        6,
    ),
    BannedRule(
        "A1_18_yaXiaLai",
        RuleCategory.TEMPLATE_BLANK,
        "压下来",
        _make_pattern_detector(r"[^。！？；\n]{1,15}压下来"),
        ("压下来",),
        "替换为更具体的动词，如施压、压制、压下、逼近等。根据语境选择，不要继续用「压下来」。",
        6,
    ),
    BannedRule(
        "A1_19_wangYa",
        RuleCategory.TEMPLATE_BLANK,
        "往...压",
        _make_pattern_detector(r"往[^。！？；\n]{1,15}压"),
        ("往", "压"),
        "改写成明确动作，不要保留「往……压」这种半模板表达。可改为施压、压迫、逼近、压制等。",
        6,
    ),
    BannedRule(
        "A1_05_zhenZhengShi_jinRu",
        RuleCategory.TEMPLATE_BLANK,
        "真正使...进入",
        _make_pattern_detector(r"真正使[^。！？；\n]{1,20}进入"),
        ("真正使", "进入"),
        "重写整句，去掉这个文艺化短语。直接写事情如何发生、如何推进、如何转入下一阶段。",
        8,
    ),
    BannedRule(
        "A1_03_changChangHuiXianBeiRen",
        RuleCategory.CROSS_SENTENCE,
        "常常会先被人...",
        _make_pattern_detector(r"常常会先被人[^。！？；\n]{0,20}"),
        ("常常会先被人",),
        "删掉这种被动开头，改成主动陈述。直接说谁做了什么、发生了什么，不要先铺设被动视角。",
        6,
    ),
    BannedRule(
        "A1_04_houLaiZhiSuoYi",
        RuleCategory.CROSS_SENTENCE,
        "后来之所以总被反复提起",
        _make_pattern_detector(r"后来之所以总被反复提起"),
        ("后来之所以总被反复提起",),
        "删除元叙事，直接说它为什么重要、在哪些场景重要、会带来什么影响。不要写「后来之所以总被反复提起」。",
        7,
    ),
    BannedRule(
        "A2_suiZhao_shiJian_deTuiQian",
        RuleCategory.PHRASE,
        "随着时间的推移",
        _make_fixed_detector("随着时间的推移"),
        ("随着时间的推移",),
        "删除时间推进式套话，改写为具体变化过程、具体时间点或具体因果。",
        4,
    ),
    BannedRule(
        "A2_zongErYanZhi",
        RuleCategory.PHRASE,
        "总而言之",
        _make_fixed_detector("总而言之"),
        ("总而言之",),
        "不要用总结性套话收束。直接给结论、判断或行动建议。",
        4,
    ),
    BannedRule(
        "A2_zhongSuoZhouZhi",
        RuleCategory.PHRASE,
        "众所周知",
        _make_fixed_detector("众所周知"),
        ("众所周知",),
        "删除空泛共识声明，直接给事实或证据。不要用「众所周知」代替论证。",
        4,
    ),
    BannedRule(
        "A2_buYanErYu",
        RuleCategory.PHRASE,
        "不言而喻",
        _make_fixed_detector("不言而喻"),
        ("不言而喻",),
        "删除这种自明式判断，改为明确解释为什么成立。",
        4,
    ),
    BannedRule(
        "A2_wuYongZhiYi",
        RuleCategory.PHRASE,
        "毋庸置疑",
        _make_fixed_detector("毋庸置疑"),
        ("毋庸置疑",),
        "不要先宣布不可争辩，直接给证据或论证。",
        4,
    ),
    BannedRule(
        "A2_youYu_ciLiang",
        RuleCategory.PHRASE,
        "在某种程度上",
        _make_fixed_detector("在某种程度上"),
        ("在某种程度上",),
        "删掉模糊限定，明确说明到底在哪些方面、到什么程度成立。",
        3,
    ),
)

_RULE_INDEX: dict[str, BannedRule] = {rule.rule_id: rule for rule in RULES}


def get_all_rules() -> tuple[BannedRule, ...]:
    """返回按 severity 降序排序的所有规则。"""
    return tuple(sorted(RULES, key=lambda r: (-r.severity, r.rule_id)))


def get_rule_by_id(rule_id: str) -> BannedRule | None:
    return _RULE_INDEX.get(rule_id)


def scan_violations(text: str) -> list[tuple[BannedRule, list[str]]]:
    """
    扫描文本，返回每条规则的命中情况。
    每个元组：(规则, 该规则命中的句子列表)
    只返回有命中的规则。
    """
    findings: list[tuple[BannedRule, list[str]]] = []
    for rule in get_all_rules():
        matches = [m.group(0).strip() for m in rule.detector_regex.finditer(text)]
        if matches:
            findings.append((rule, matches))
    return findings


__all__ = ["BannedRule", "RuleCategory", "get_all_rules", "get_rule_by_id", "scan_violations"]
