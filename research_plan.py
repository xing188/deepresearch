from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated


class ResearchPlan(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    objective: str = Field(min_length=2, max_length=1500, description="具体研究目标与范围，使用中文")
    outline: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(min_length=1, max_length=8, description="最多8个报告章节标题")
    focus: str = Field(default="", max_length=1500, description="优先回答的问题、比较维度或时间范围")
    exclusions: str = Field(default="", max_length=1000, description="明确不研究的内容，避免扩展过多")
    source_preferences: str = Field(default="优先原始论文、官方文档与公开评测。", max_length=1000)


def plan_brief(plan: dict) -> str:
    return "\n\n".join([
        "研究目标\n" + plan["objective"],
        "已确认的报告提纲\n" + "\n".join(f"{i}. {section}" for i, section in enumerate(plan["outline"], 1)),
        "研究重点\n" + (plan.get("focus") or "围绕研究目标展开"),
        "排除内容\n" + (plan.get("exclusions") or "不扩展与研究目标无关的话题"),
        "来源要求\n" + plan.get("source_preferences", ""),
    ])
