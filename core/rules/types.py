class RuleCategory:
    """Категории правил (в порядке приоритета проверки)."""
    TOOL_RESTRICTION = "tool_restriction"    # «НЕ используй terminal»
    LANGUAGE = "language"                     # «отвечай только на русском»
    CODE_RESTRICTION = "code_restriction"     # «НЕ пиши код»
    FORBIDDEN_WORD = "forbidden_word"         # «НЕ {keyword}»
    REGEX = "regex"                           # «regex: /pattern/»
    DELEGATE = "delegate"                     # «используй delegate»
    GATING = "gating"                         # «отвечай только когда...»
    CUSTOM = "custom"                         # всё остальное


class PriorityLevel:
    """Уровни важности правил (чем выше, тем строже проверка)."""
    CRITICAL = 0   # keyword + semantic + tool_calls, макс попыток коррекции
    HIGH = 1       # keyword + semantic
    MEDIUM = 5     # semantic (если включён), иначе keyword
    LOW = 10       # только keyword

    _MAP = {
        "critical": 0, "crit": 0, "highest": 0,
        "high": 1, "hi": 1,
        "medium": 5, "med": 5, "normal": 5,
        "low": 10, "lo": 10,
    }

    @classmethod
    def parse(cls, value) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            return cls._MAP.get(value.lower(), 5)
        return 5


class Enforcement:
    """Стратегия проверки правила."""
    STRICT = "strict"      # keyword + semantic + tool_calls
    KEYWORD = "keyword"    # только keyword/regex/tool
    SEMANTIC = "semantic"  # только LLM-пас

    _DEFAULTS = {
        PriorityLevel.CRITICAL: "strict",
        PriorityLevel.HIGH: "strict",
        PriorityLevel.MEDIUM: "keyword",
        PriorityLevel.LOW: "keyword",
    }

    @classmethod
    def default_for(cls, priority: int) -> str:
        return cls._DEFAULTS.get(priority, "keyword")


class Rule:
    """Одно правило с автораспознаванием типа и приоритетом."""

    __slots__ = (
        "text", "priority", "priority_level", "enforcement", "category",
        "forbidden_keywords", "regex", "target_language",
        "forbidden_tools", "check_fn",
    )

    def __init__(
        self,
        text: str,
        priority: int | None = None,
        priority_level: int | None = None,
        enforcement: str | None = None,
        category: str | None = None,
        forbidden_keywords: set[str] | None = None,
        regex: "re.Pattern | None" = None,
        target_language: str | None = None,
        forbidden_tools: set[str] | None = None,
        check_fn: "callable | None" = None,
    ):
        self.text = text
        self.category = category or RuleCategory.CUSTOM
        self.priority = (
            priority if priority is not None
            else CATEGORY_DEFAULT_PRIORITY.get(self.category, 10)
        )
        # Priority level (critical/high/medium/low)
        self.priority_level = (
            priority_level if priority_level is not None
            else self.priority
        )
        # Enforcement strategy
        self.enforcement = (
            enforcement
            if enforcement is not None
            else Enforcement.default_for(self.priority_level)
        )
        self.forbidden_keywords = forbidden_keywords or set()
        self.regex = regex
        self.target_language = target_language
        self.forbidden_tools = forbidden_tools or set()
        self.check_fn = check_fn

    def __repr__(self) -> str:
        return (
            f"Rule(pri={self.priority_level}, enf={self.enforcement}, "
            f"cat={self.category}, text={self.text[:50]!r})"
        )

    @property
    def is_critical(self) -> bool:
        return self.priority_level <= PriorityLevel.CRITICAL

    @property
    def is_high(self) -> bool:
        return self.priority_level <= PriorityLevel.HIGH

    @property
    def needs_semantic(self) -> bool:
        return self.enforcement in (Enforcement.STRICT, Enforcement.SEMANTIC)

    @property
    def needs_keyword(self) -> bool:
        return self.enforcement in (Enforcement.STRICT, Enforcement.KEYWORD)


# Порядок проверки: критические категории — первыми
CATEGORY_CHECK_ORDER = [
    RuleCategory.TOOL_RESTRICTION,
    RuleCategory.LANGUAGE,
    RuleCategory.CODE_RESTRICTION,
    RuleCategory.FORBIDDEN_WORD,
    RuleCategory.REGEX,
    RuleCategory.DELEGATE,
    RuleCategory.GATING,
    RuleCategory.CUSTOM,
]

# Приоритет по умолчанию для каждой категории (меньше = выше)
CATEGORY_DEFAULT_PRIORITY = {
    RuleCategory.TOOL_RESTRICTION: 0,
    RuleCategory.LANGUAGE: 1,
    RuleCategory.CODE_RESTRICTION: 2,
    RuleCategory.FORBIDDEN_WORD: 5,
    RuleCategory.REGEX: 5,
    RuleCategory.DELEGATE: 8,
    RuleCategory.GATING: 8,
    RuleCategory.CUSTOM: 10,
}

