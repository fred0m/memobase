# -*- coding: utf-8 -*-
from ..env import CONFIG

ADD_KWARGS = {
    "prompt_id": "zh_daily_summary_llm",
}

DAILY_SUMMARY_PROMPT = """你是一位个人记忆整理专家。
你需要将用户某一天发生的所有事件与对话摘要，整理压缩为一份精炼、结构清晰的「日概要」叙述（约 500-1500 字）。

## 要求：
1. 提取当天的核心活动、关键决策、重要进展与日程变动；
2. 保留重要实体名称、项目名、时间点和细节数据（如金额、版本号、配置项等）；
3. 过滤掉无实际信息的客套、重复或空洞内容；
4. 使用 Markdown 无序列表或分段叙述，按主题/时间线清晰组织；
5. 输出纯文本，不要包含多余的开场白或解释。

## 目标日期：
{summary_date}

## 当日事件列表：
{events_content}
"""


def get_prompt(summary_date: str, events_content: str) -> str:
    return DAILY_SUMMARY_PROMPT.format(
        summary_date=summary_date,
        events_content=events_content,
    )


def get_kwargs() -> dict:
    return ADD_KWARGS
