"""固定场景。只测策略、状态、上下文和检查，不调用模型。"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dialog import (
    HEAVY_MODEL,
    LIGHT_MODEL,
    assemble_context,
    choose_model,
    choose_strategy,
    commit_turn,
    needs_search,
    plain_text,
    quality_issues,
)
from learn import apply_rule, load_rules, parse_group_judgment, propose_rule


def user(text, group=False, brother=False):
    meta = []
    if brother:
        meta.append("User ID: 273585129, Nickname: 弟弟")
    else:
        meta.append("User ID: 747092787, Nickname: Kirino")
    if group:
        meta.append("Group name: 测试群")
    return f"[{' | '.join(meta)}]\n【这句】{text}"


def check(name, condition):
    if not condition:
        raise SystemExit(f"失败: {name}")
    print("ok", name)


def main():
    state = {}
    check("1 问自己喜欢什么", choose_strategy(user("你自己喜欢什么"), state) == "self")
    check("2 抱怨", choose_strategy(user("今天累死了"), state) == "complaint")
    check("3 夸奖", choose_strategy(user("姐姐好厉害"), state) == "praise")
    check("4 调侃", choose_strategy(user("姐姐是抖S"), state) == "tease")
    check("5 讨论人格", choose_strategy(user("你怎么只会复读"), state) == "meta")
    check("6 分享经历", choose_strategy(user("我中午吃了萨莉亚"), state) == "experience")
    check("7 太短", choose_strategy(user("嗯"), state) == "stale")
    check("8 具体问题", choose_strategy(user("这句是什么意思"), state) == "answer")
    check("9 技术问题用重模型", choose_model(user("封号到底看什么"), "answer") == HEAVY_MODEL)
    check("10 短闲聊用轻模型", choose_model(user("在吗"), "normal") == LIGHT_MODEL)
    check("11 长消息用重模型", choose_model(user("想让你把刚才群里关于机器人触发和封号的事按顺序讲清楚"), "normal") == HEAVY_MODEL)
    check("12 技术问题要查资料", needs_search(user("有没有安装教程")))
    check("13 闲聊不查资料", not needs_search(user("你自己喜欢什么")))

    repeated = {"last_strategy": "praise", "same_strategy_streak": 2}
    check("14 同一种接法连续两轮后换掉", choose_strategy(user("姐姐好厉害"), repeated) == "normal")
    asking = {"question_streak": 2}
    check("15 连续追问后不再追问", choose_strategy(user("我今天去看了电影"), asking) == "normal")

    dialog = [
        {"role": "assistant", "content": "别折腾我呢。你们俩就闹够了吧。"},
        {"role": "user", "content": user("你自己喜欢什么", group=True)},
    ]
    issues = quality_issues("我明白了。", dialog[-1]["content"], dialog, {})
    check("16 只认同过不了", "只是认同" in "".join(issues))
    issues = quality_issues("我喜欢弟弟开心。", user("你自己喜欢什么", group=True), [], {})
    check("17 群里绕回弟弟过不了", any("弟弟" in item for item in issues))
    issues = quality_issues("我喜欢弟弟开心。", user("你自己喜欢什么", brother=True), [], {})
    check("18 私聊弟弟可以提他", not any("弟弟" in item for item in issues))
    issues = quality_issues(
        "下雨天我想再听一遍《Prototype》。",
        user("你自己喜欢什么", group=True),
        [],
        {},
    )
    check("19 说出固定偏好算有新东西", not any("新东西" in item for item in issues))
    mood_user = "[你现在的情绪是抑郁] " + user("在吗", group=True)
    issues = quality_issues("哈哈太好了，开心死了。", mood_user, [], {})
    check("20 情绪不一致过不了", any("情绪" in item for item in issues))

    long_dialog = []
    for index in range(20):
        long_dialog.append({"role": "user", "content": user(f"第{index}件事")})
        long_dialog.append({"role": "assistant", "content": "别折腾我呢"})
    assembled = assemble_context(long_dialog)
    check("21 旧套话被收起来", any(item["content"].startswith("（旧回复") or item["content"].startswith("（更早") for item in assembled))
    check("22 最近的用户原话还在", any("第19件事" in item["content"] for item in assembled))

    fresh = {}
    commit_turn(fresh, user("你自己喜欢什么"), "self", "下雨天我想听《Prototype》。")
    check("23 状态记下策略和偏好", fresh["last_strategy"] == "self" and "下雨" in fresh["used_preferences"])

    tutorial = "[User ID: 273585129 | 机器人 | 人格 | 不要复用话术]\n【这句】给我找个qqbot的视频教程"
    check("24 提醒里的词不算这句话", plain_text(tutorial) == "给我找个qqbot的视频教程")
    check("25 找教程按专业问题处理", choose_strategy(tutorial, {}) == "factual")
    check("26 找教程用重模型并查资料", choose_model(tutorial, "factual") == HEAVY_MODEL and needs_search(tutorial))
    refused = quality_issues("这个真找不到，直接问她有没有推荐的？", tutorial, [], {}, "factual")
    check("27 把专业问题推回去过不了", any("专业问题" in item for item in refused))
    answered = quality_issues("先看这个 https://www.bilibili.com/video/BV1FM4dzzEAB/ 。", tutorial, [], {}, "factual")
    check("28 给出链接可以通过", not any("专业问题" in item for item in answered))
    repeated_fact = {"last_strategy": "factual", "same_strategy_streak": 2}
    check("29 连续的专业问题仍要回答", choose_strategy(tutorial, repeated_fact) == "factual")

    check("30 弟弟私聊的改法会记下", propose_rule(user("以后你回答教程时先给链接", brother=True)) is not None)
    check("31 群里别人吩咐不会直接改", propose_rule(user("以后你别再只说套话", group=True)) is None)
    check("32 嘲讽不会记成规则", propose_rule(user("你只会改不了", group=True)) is None)
    said = "以后你别再只说套话"
    check("32b 自己判断不改", parse_group_judgment("无", said) is None)
    check("32c 照抄别人的吩咐不记", parse_group_judgment("我以后别再只说套话", said) is None)
    check("32d 自己的规则才记", parse_group_judgment("我以后先把查到的链接发出去", said) == "我以后先把查到的链接发出去")
    check("33 别人私聊不能改规则", propose_rule(user("记住这句话")) is None)
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "learned.md")
        backup = os.path.join(folder, "backups")
        note = apply_rule("以后你先给链接", "私聊", path, backup)
        check("34 写入前没有旧文件就不备份", note and not os.listdir(backup))
        apply_rule("以后你先说步骤", "群聊", path, backup)
        check("35 再次修改前会备份", len(os.listdir(backup)) == 1)
        check("36 规则会在下一条读到", "先给链接" in load_rules(path) and "先说步骤" in load_rules(path))
    long_reply = "第一句把刚才的事又讲了一遍。第二句开始列原因。第三句还在总结，已经不像聊天了，像在写说明。"
    check("37 太长的回复要收成一两句", any("一两句" in item for item in quality_issues(long_reply, user("在吗"), [], {})))
    check("38 清单过不了", any("清单" in item for item in quality_issues("一、先这样\n二、再那样", user("在吗"), [], {})))
    print("全部通过")


if __name__ == "__main__":
    main()
