from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.banned_rules import get_all_rules, get_rule_by_id, scan_violations


POSITIVE_CASES: list[tuple[str, str]] = [
    ("A1_01_buShi_erShi", "这不是简单的问题，而是结构性的问题。"),
    ("A1_25_xiangShiZai_gengXiangShi", "这像是在解释，更像是在回避。"),
    ("A2_yuQiShuo_buRuShuo", "与其说他在解释，不如说他在回避。"),
    ("A2_kanShi_shiZe", "他们看似在合作，实则各怀心思。"),
    ("A2_jiShi_yeShi", "这既是选择，也是代价。"),
    ("A2_biaoMianShang_benZhiShang", "表面上是合作，本质上是竞争。"),
    ("A1_14_tuoZhu", "他用力托住那块石头。"),
    ("A1_22_ningDaoYiQi", "两件事被拧到了一起。"),
    ("A1_11_yeJiuShiShuo", "也就是说，这件事很复杂。"),
    ("A1_20_baHuaShuoTou", "把话说透之后，事情就清楚了。"),
    ("A1_21_baHuaZhiBai", "把话说得再直白一点，其实就是这样。"),
    ("A1_29_zhiBaiYiDian", "直白一点讲，这就是代价。"),
    ("A1_28_wenTiGuanJian", "问题的关键在于执行。"),
    ("A1_24_diChuShui", "她激动得能滴出水。"),
    ("A1_30_baLianTiao", "我们把这条链条慢慢展开。"),
    ("A1_26_muBiaoZhiJie", "我们的目标很直接，就是把它做好。"),
    ("A1_08_zaiWangShenChu", "再往深处看，答案并不复杂。"),
    ("A1_09_zhiSuoYiZhiDe", "之所以值得写，是因为它影响很大。"),
    ("A1_10_henNengShuoMing", "这个变化很能说明问题。"),
    ("A1_33_dingDeHenSi", "规则定得很死，不能改。"),
    ("A1_35_xianShiGan", "这是一个很有现实感的判断。"),
    ("A1_23_feiChangShiJi", "这是一个非常实际的方案。"),
    ("A1_12_feiChangYing", "态度非常硬的。"),
    ("A1_13_henYing", "他的语气很硬的。"),
    ("A1_15_henDuoYiTiDao", "很多人一提到这个话题就沉默。"),
    ("A1_07_lianQiLai", "把几件事连起来看，就会更清楚。"),
    ("A1_06_wangYiQiTui", "把前后逻辑往一起推，就能发现问题。"),
    ("A1_34_zaiChenXiaQu", "最后让情绪在文字里沉下去。"),
    ("A1_32_huiYueLaiYueShun", "后面的推进会越来越顺。"),
    ("A1_02_ruGuoZhiFenLiang", "如果只看结果，分量就轻了。"),
    ("A1_27_yiBuBuLaZhi", "我们一步步把关系拉直。"),
    ("A1_31_baSongGuoQu", "把材料送过去。"),
    ("A1_16_changChuLai", "新的秩序长出来了。"),
    ("A1_17_jieZhu", "这句话接住了前面的情绪。"),
    ("A1_18_yaXiaLai", "压力被压下来以后，问题才暴露。"),
    ("A1_19_wangYa", "对供应链往下游压。"),
    ("A1_05_zhenZhengShi_jinRu", "真正使项目进入正轨的是执行。"),
    ("A1_03_changChangHuiXianBeiRen", "常常会先被人忽略的，反而最重要。"),
    ("A1_04_houLaiZhiSuoYi", "后来之所以总被反复提起，是因为它改变了方法。"),
    ("A2_suiZhao_shiJian_deTuiQian", "随着时间的推移，变化越来越明显。"),
    ("A2_zongErYanZhi", "总而言之，这件事需要重做。"),
    ("A2_zhongSuoZhouZhi", "众所周知，这个问题一直存在。"),
    ("A2_buYanErYu", "不言而喻，这样做更稳妥。"),
    ("A2_wuYongZhiYi", "毋庸置疑，这是一种更好的方法。"),
    ("A2_youYu_ciLiang", "在某种程度上，这个判断成立。"),
]

NEGATIVE_CASES = [
    "正常文本没有违规。",
    "我们讨论的是一个普通项目。",
    "这件事需要更具体的分析。",
]


def test_rule_catalog_size_and_lookup() -> None:
    rules = get_all_rules()
    assert len(rules) >= 35
    assert get_rule_by_id("A1_01_buShi_erShi") is not None
    assert get_rule_by_id("missing") is None


def test_key_acceptance_cases() -> None:
    assert scan_violations("这不是简单的问题，而是结构性的问题。")
    assert scan_violations("他们看似在合作，实则各怀心思。")
    assert scan_violations("正常文本没有违规。") == []


def test_every_rule_has_positive_and_negative_case() -> None:
    rule_ids = {rule.rule_id for rule in get_all_rules()}
    positive_ids = {rule_id for rule_id, _ in POSITIVE_CASES}
    assert rule_ids.issubset(positive_ids)

    for rule_id, text in POSITIVE_CASES:
        hits = scan_violations(text)
        assert any(rule.rule_id == rule_id for rule, _ in hits), rule_id

    for text in NEGATIVE_CASES:
        hits = scan_violations(text)
        assert all(text not in samples for _, samples in hits)


def test_each_rule_specific_positive_and_negative() -> None:
    for rule_id, positive_text in POSITIVE_CASES:
        rule = get_rule_by_id(rule_id)
        assert rule is not None
        assert any(rule.rule_id == r.rule_id for r, _ in scan_violations(positive_text))

        if rule.rule_id == "A1_01_buShi_erShi":
            negative_text = "这是简单的问题，也是结构性的问题。"
        elif rule.rule_id == "A2_kanShi_shiZe":
            negative_text = "他们在合作，彼此分工明确。"
        else:
            negative_text = f"正常句子：{rule_id} 不应误报。"
            for banned in rule.forbidden_substrings:
                negative_text = negative_text.replace(banned, "替换")

        assert all(rule.rule_id != r.rule_id for r, _ in scan_violations(negative_text))
