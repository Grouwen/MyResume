from typing import TypedDict


class StatisticsState(TypedDict):
    input_tokens: int # 本turn输入token
    output_tokens: int # 本turn输出token
    tool_fail_count: int # 调用工具失败次数