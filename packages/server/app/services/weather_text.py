"""WMO 天气代码 → 中文文案。"""

_TEXT: dict[int, str] = {
    0: "晴",
    1: "晴",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "雾",
    51: "毛毛雨",
    53: "毛毛雨",
    55: "毛毛雨",
    56: "冻雨",
    57: "冻雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "阵雪",
    86: "阵雪",
    95: "雷暴",
    96: "雷暴伴冰雹",
    99: "雷暴伴冰雹",
}


def describe(code: int | None) -> str | None:
    if code is None:
        return None
    return _TEXT.get(int(code), "多云")


def describe_transition(now_code: int | None, later_code: int | None) -> str | None:
    """「晴转多云」：当前与几小时后不同才写「转」，相同只写一个。"""
    a, b = describe(now_code), describe(later_code)
    if a is None:
        return None
    if b is None or b == a:
        return a
    return f"{a}转{b}"
