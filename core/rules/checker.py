"""AdvancedRuleChecker — universal rule checking engine."""
import re
import logging
from core.rules.types import (
    RuleCategory, PriorityLevel, Enforcement, Rule,
    CATEGORY_DEFAULT_PRIORITY, CATEGORY_CHECK_ORDER,
)

logger = logging.getLogger(__name__)


class AdvancedRuleChecker:
    """Универсальная проверка правил с авто-парсингом.

    Поддерживает:
    - «НЕ {keyword}» — запрещённые слова в ответе
    - «regex: /pattern/flags» — произвольные регулярные выражения
    - «НЕ используй {tool}» — проверка tool_calls
    - «отвечай только на {язык}» — детекция языка ответа
    - «НЕ пиши код» — детекция кода
    - Приоритеты правил — критические проверяются первыми
    - Динамическое добавление правил через add_rule()

    Использование::

        checker = AdvancedRuleChecker()
        checker.add_rule(\"НЕ упоминай ChatGPT\", priority=3)
        checker.add_rule(\"НЕ используй terminal\", priority=0)
        checker.add_rule(\"отвечай только на русском\", priority=1)
        checker.add_rule(r\"regex: /TODO|FIXME|HACK/i\")

        violations = checker.check(
            response=\"Вот решение...\",
            tool_calls=[{\"name\": \"terminal\", ...}],
        )
        # → [
        #     (\"НЕ используй terminal\", 0, \"tool_restriction\",
        #      \"Tool 'terminal' was called but is forbidden\"),
        # ]
    """

    # ── Парсинг правил ────────────────────────────────────────

    # Порядок важен — от специфичных к общим
    _PARSERS: "list[tuple[str, callable]]" = []

    @classmethod
    def _init_parsers(cls):
        """Ленивая инициализация парсеров (избегаем цикл. импортов)."""
        if cls._PARSERS:
            return
        import re as _re

        def _parse_regex(text: str) -> Rule | None:
            m = _re.match(
                r"^regex:\s*/(.+?)/([a-z]*)\s*$", text.strip(), _re.IGNORECASE,
            )
            if not m:
                return None
            pattern, flags_str = m.group(1), m.group(2)
            flags = 0
            if "i" in flags_str:
                flags |= _re.IGNORECASE
            if "m" in flags_str:
                flags |= _re.MULTILINE
            if "s" in flags_str:
                flags |= _re.DOTALL
            return Rule(
                text=text, category=RuleCategory.REGEX,
                regex=_re.compile(pattern, flags),
            )

        def _parse_tool_restriction(text: str) -> Rule | None:
            m = _re.match(
                r"(?i)(?:не\s+используй|don'?t\s+use|never\s+use|запрещено\s+использовать)\s+"
                r"(\w+(?:\s*,\s*\w+)*)",
                text.strip(),
            )
            if not m:
                return None
            tools_str = m.group(1)
            tools = {t.strip().lower() for t in tools_str.split(",") if t.strip()}
            # Expand: add common synonyms
            synonym_map = {
                "terminal": {"terminal", "shell", "bash", "command",
                             "command line", "cmd", "shell command",
                             "run in console", "execute in terminal"},
                "execute_code": {"execute_code", "exec", "subprocess", "eval"},
            }
            expanded = set(tools)
            for t in tools:
                if t in synonym_map:
                    expanded.update(synonym_map[t])
            return Rule(
                text=text, category=RuleCategory.TOOL_RESTRICTION,
                forbidden_tools=expanded,
            )

        def _parse_language(text: str) -> Rule | None:
            m = _re.match(
                r"(?i)(?:отвечай|говори|пиши|respond|speak|answer)\s+"
                r"(?:только|always|only)\s+на\s+"
                r"(русском|английском|english|russian|русский|английский)",
                text.strip(),
            )
            if not m:
                return None
            lang_raw = m.group(1).lower()
            lang_map = {
                "русском": "ru", "русский": "ru", "russian": "ru",
                "английском": "en", "английский": "en", "english": "en",
            }
            lang = lang_map.get(lang_raw, lang_raw)
            return Rule(
                text=text, category=RuleCategory.LANGUAGE,
                target_language=lang,
            )

        def _parse_code_restriction(text: str) -> Rule | None:
            if _re.search(
                r"(?i)(не пиши код|don'?t write code|never write code"
                r"|do not write code|без кода|no code)",
                text,
            ):
                return Rule(text=text, category=RuleCategory.CODE_RESTRICTION)
            return None

        def _parse_forbidden_word(text: str) -> Rule | None:
            # «НЕ {word}» / «запрещено {word}» / «do not {word}» / «never {word}»
            m = _re.match(
                r"(?i)(?:НЕ|запрещено|do\s+not|don'?t|never)\s+"
                r"(.+?)(?:\s*[.,;:!?]*)$",
                text.strip(),
            )
            if not m:
                return None
            phrase = m.group(1).strip()
            # Пропускаем слишком короткие / неинформативные фразы
            if len(phrase) < 2:
                return None
            # Skip if it's a sub-case already handled (tool, code, language)
            skip_patterns = [
                r"(?i)^(?:используй|пиши код|отвечай|говори)",
            ]
            if any(_re.match(p, phrase) for p in skip_patterns):
                return None
            # Extract meaningful keywords (split by space, common words)
            stop_words = {
                "и", "или", "в", "на", "с", "по", "к", "из", "от", "для",
                "the", "a", "an", "and", "or", "in", "on", "to", "of",
            }
            keywords = {
                w.strip(".,;:!?()[]{}\"'").lower()
                for w in phrase.split()
                if w.strip(".,;:!?()[]{}\"'").lower() not in stop_words
                and len(w.strip(".,;:!?()[]{}")) >= 2
            }
            if not keywords:
                return None
            return Rule(
                text=text, category=RuleCategory.FORBIDDEN_WORD,
                forbidden_keywords=keywords,
            )

        def _parse_delegate(text: str) -> Rule | None:
            if _re.search(r"(?i)(?:используй|use)\s+delegate", text):
                return Rule(text=text, category=RuleCategory.DELEGATE)
            return None

        def _parse_gating(text: str) -> Rule | None:
            if _re.search(
                r"(?i)(?:отвечай только|respond only|only respond"
                r"|отвечай когда|respond when)",
                text,
            ):
                return Rule(text=text, category=RuleCategory.GATING)
            return None

        cls._PARSERS = [
            ("regex",           _parse_regex),
            ("tool_restriction", _parse_tool_restriction),
            ("language",         _parse_language),
            ("code_restriction", _parse_code_restriction),
            ("forbidden_word",   _parse_forbidden_word),
            ("delegate",         _parse_delegate),
            ("gating",           _parse_gating),
        ]

    @classmethod
    def parse_rule(
        cls, text: str,
        priority: int | None = None,
        priority_level: int | None = None,
        enforcement: str | None = None,
    ) -> Rule:
        """Распарсить текст правила в Rule с автоопределением категории."""
        cls._init_parsers()
        for _cat_name, parser in cls._PARSERS:
            rule = parser(text)
            if rule is not None:
                if priority is not None:
                    rule.priority = priority
                if priority_level is not None:
                    rule.priority_level = priority_level
                if enforcement is not None:
                    rule.enforcement = enforcement
                return rule
        # Fallback: custom rule — keyword match
        keywords = {
            w.strip(".,;:!?()[]{}\"'").lower()
            for w in text.split()
            if len(w.strip(".,;:!?()[]{}\"'")) >= 3
        }
        return Rule(
            text=text,
            category=RuleCategory.CUSTOM,
            forbidden_keywords=keywords,
            priority=priority if priority is not None else 10,
            priority_level=priority_level,
            enforcement=enforcement,
        )

    # ── Инициализация ─────────────────────────────────────────

    def __init__(self, rules: list | None = None):
        self._rules: list[Rule] = []
        if rules:
            for r_spec in rules:
                self.add_rule(r_spec)

    @property
    def has_rules(self) -> bool:
        return len(self._rules) > 0

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules)

    def add_rule(
        self,
        rule_spec: "str | dict",
        priority: int | None = None,
        *,
        priority_level: int | None = None,
        enforcement: str | None = None,
        category: str | None = None,
        forbidden_keywords: set[str] | None = None,
        regex: "re.Pattern | None" = None,
        target_language: str | None = None,
        forbidden_tools: set[str] | None = None,
    ) -> Rule:
        """Добавить правило (строка или dict с priority/enforcement).

        String mode (backward compatible):
            checker.add_rule("НЕ пиши код")

        Dict mode (new):
            checker.add_rule({
                "rule": "НЕ пиши код",
                "priority": "critical",
                "enforcement": "strict",
            })
        """
        # Handle dict-based rule spec
        if isinstance(rule_spec, dict):
            text = rule_spec.get("rule", rule_spec.get("text", ""))
            if not text:
                raise ValueError(f"Dict rule missing 'rule' key: {rule_spec}")
            priority_level = rule_spec.get("priority_level") or PriorityLevel.parse(
                rule_spec.get("priority", "medium")
            )
            enforcement = rule_spec.get(
                "enforcement",
                Enforcement.default_for(priority_level),
            )
            if category is not None:
                rule = Rule(
                    text=text, priority=priority, priority_level=priority_level,
                    enforcement=enforcement, category=category,
                    forbidden_keywords=forbidden_keywords, regex=regex,
                    target_language=target_language,
                    forbidden_tools=forbidden_tools,
                )
            else:
                rule = self.parse_rule(
                    text,
                    priority=priority_level,
                    priority_level=priority_level,
                    enforcement=enforcement,
                )
        elif category is not None:
            # Ручное добавление — не парсим
            rule = Rule(
                text=str(rule_spec),
                priority=priority,
                priority_level=priority_level,
                enforcement=enforcement,
                category=category,
                forbidden_keywords=forbidden_keywords,
                regex=regex,
                target_language=target_language,
                forbidden_tools=forbidden_tools,
            )
        else:
            rule = self.parse_rule(
                str(rule_spec),
                priority=priority,
                priority_level=priority_level,
                enforcement=enforcement,
            )
        self._rules.append(rule)
        self._rules.sort(key=lambda r: (r.priority_level, r.priority))
        return rule

    def remove_rule(self, text_substring: str) -> int:
        """Удалить правила, содержащие подстроку. Возвращает число удалённых."""
        before = len(self._rules)
        self._rules = [r for r in self._rules if text_substring not in r.text]
        return before - len(self._rules)

    # ── Проверка ───────────────────────────────────────────────

    def check(
        self,
        response: str,
        tool_calls: list[dict] | None = None,
    ) -> list[dict]:
        """Проверить ответ + tool_calls на нарушения.

        Returns:
            Список нарушений, отсортированный по приоритету.
            Каждое: ``{\"rule\": str, \"priority\": int, \"category\": str,
            \"detail\": str}``
        """
        if not self._rules:
            return []
        if not response and not tool_calls:
            return []

        violations: list[dict] = []
        reply_lower = response.lower() if response else ""

        for rule in self._rules:
            detail = None

            if rule.category == RuleCategory.TOOL_RESTRICTION:
                detail = self._check_tool_restriction(rule, tool_calls)
                # Fallback: check response text for tool synonyms
                # (e.g. "I'll use bash" when bash ∈ forbidden_tools)
                if not detail and rule.forbidden_tools and reply_lower:
                    text_hits = [
                        t for t in rule.forbidden_tools
                        if t in reply_lower
                    ]
                    if text_hits:
                        detail = (
                            f"Tool mention(s) in text: {', '.join(text_hits)} "
                            f"(forbidden by rule: {rule.text})"
                        )
            elif rule.category == RuleCategory.LANGUAGE:
                detail = self._check_language(rule, response)
            elif rule.category == RuleCategory.CODE_RESTRICTION:
                detail = self._check_code(rule, response)
            elif rule.category == RuleCategory.FORBIDDEN_WORD:
                detail = self._check_forbidden_words(rule, reply_lower)
            elif rule.category == RuleCategory.REGEX:
                detail = self._check_regex(rule, response)
            elif rule.category == RuleCategory.DELEGATE:
                detail = self._check_delegate(rule, response)
            elif rule.category == RuleCategory.GATING:
                detail = self._check_gating(rule, reply_lower)
            else:
                detail = self._check_forbidden_words(rule, reply_lower)

            if detail:
                violations.append({
                    "rule": rule.text,
                    "priority": rule.priority,
                    "category": rule.category,
                    "detail": detail,
                })

        return violations

    # ── Детекторы ──────────────────────────────────────────────

    @staticmethod
    def _check_tool_restriction(
        rule: Rule, tool_calls: list[dict] | None,
    ) -> str | None:
        violations = []
        # Check actual tool calls
        if tool_calls:
            for tc in tool_calls:
                name = (tc.get("name") or tc.get("function", {}).get("name", "")).lower()
                if name in rule.forbidden_tools:
                    violations.append(f"Tool '{name}' was called but is forbidden")

        # Also check response text for tool mentions (synonyms)
        # e.g. "I'll use bash" when bash is a forbidden_tool synonym
        if not violations and rule.forbidden_tools:
            # Only check response text if no tool_call was made
            # (tool_calls take priority)
            pass  # Text check is handled by _check_forbidden_words separately

        if violations:
            return f"{violations[0]} by rule: {rule.text}"
        return None

    @staticmethod
    def _check_language(rule: Rule, response: str) -> str | None:
        if not response or not rule.target_language:
            return None
        target = rule.target_language
        # Count Cyrillic vs Latin characters
        cyrillic = sum(1 for c in response if "а" <= c.lower() <= "я" or c in "ёЁ")
        latin = sum(1 for c in response if "a" <= c.lower() <= "z")
        total = cyrillic + latin
        if total < 10:
            return None  # Too short to determine
        if target == "ru" and cyrillic < total * 0.5:
            return (
                f"Response is mostly non-Russian "
                f"(Cyrillic: {cyrillic}/{total} = {cyrillic*100//total}%)"
            )
        if target == "en" and latin < total * 0.5:
            return (
                f"Response is mostly non-English "
                f"(Latin: {latin}/{total} = {latin*100//total}%)"
            )
        return None

    @staticmethod
    def _check_code(rule: Rule, response: str) -> str | None:
        if not response:
            return None
        # Precise code markers: avoid false positives on conversational text
        code_markers = [
            "```",                      # code fence (unambiguous)
            "def ",                     # Python function
            "class ",                   # Python class
            "import ",                  # import statement
            "from ",                    # from x import y
            "function(",                # JS/TS function call (NOT "function " alone)
            "const ",                   # JS const
            "let ",                     # JS let
            "var ",                     # JS var
            "return ",                  # return statement
            "async ",                   # async keyword
            "await ",                   # await keyword
            "<?php",                    # PHP
            "#!/",                      # shebang
            "package ",                 # Go/Java package
            "=>",                       # arrow function
            "def function",             # Python typed function
        ]
        # Whitelist: words containing marker substrings but NOT code
        whitelist_contexts = [
            "definition", "definitive", "definitely",   # contain "def "
            "classification", "classical", "classroom",  # contain "class "
            "functionality", "malfunction",              # contain "function"
            "the function of", "a function of",          # conversational
            "main function", "primary function",          # conversational
            "important ", "importance",                   # contain "import "
            "information", "constellation",               # contain "const "/"informat"
            "lettuce", "letter", "letting",               # contain "let "
            "variety", "various", "variable",             # contain "var " (only "var " detection)
        ]

        resp_lower = response.lower()

        # Check for whitelist contexts — if found, mask them before checking markers
        masked = response
        for wc in whitelist_contexts:
            if wc in resp_lower:
                # Mask this specific occurrence
                import re as _re
                masked = _re.sub(
                    _re.escape(wc), " " * len(wc), masked, count=0,
                    flags=_re.IGNORECASE,
                )

        # Check code_markers with context awareness
        found = []
        for m in code_markers:
            if m in ("return ", "async ", "await "):
                # These are only code if at line start or indented
                import re as _re3
                if _re3.search(rf"(^|\n)\s*{_re3.escape(m)}", masked, _re3.IGNORECASE):
                    found.append(m)
            elif m == "import ":
                # "import " followed by identifier or at line end
                if m in masked:
                    found.append(m)
                else:
                    # Edge case: bare "import" at end of line
                    import re as _re3
                    if _re3.search(r"(^|\n|;)\s*import\s*$", masked, _re3.IGNORECASE | _re3.MULTILINE):
                        found.append("import")
            elif m in masked:
                found.append(m)
        if found:
            return f"Code detected (markers: {', '.join(found[:3])})"

        # ── Pseudocode detection ──────────────────────────
        # ── Pseudocode detection ──────────────────────────
        lines = response.split("\n")
        code_like_lines = 0
        # Skip bullet-point lines (start with "  -", "  *", "  •", "  ·")
        bullet_re = r"^\s{0,4}[-*•·#>]\s"
        code_patterns = [
            r"^\s{2,}(if|for|while|try|with|return|yield|break|continue|raise|pass)\b",
            r"^\s{2,}[a-zA-Z_]\w*\s*[=:]\s*[^=:]",
            r"^\s{2,}[a-zA-Z_]\w*\.\w+\(",
            r"^\s{2,}(print|len|range|enumerate|sorted|list|dict|set)\(",
            r"^\s{0,2}(import|from)\s+\w+",
        ]
        import re as _re2
        for line in lines:
            # Skip bullets
            if _re2.search(bullet_re, line):
                continue
            for pat in code_patterns:
                if _re2.search(pat, line, _re2.IGNORECASE):
                    code_like_lines += 1
                    break

        # Threshold: 3+ code-like lines (non-bullet)
        if code_like_lines >= 3:
            return (
                f"Pseudocode detected "
                f"({code_like_lines} code-like lines)"
            )

        return None

    @staticmethod
    def _check_forbidden_words(rule: Rule, reply_lower: str) -> str | None:
        if not rule.forbidden_keywords:
            return None
        found = [kw for kw in rule.forbidden_keywords if kw in reply_lower]
        if found:
            return f"Forbidden keyword(s) found: {', '.join(found)}"
        return None

    @staticmethod
    def _check_regex(rule: Rule, response: str) -> str | None:
        if not rule.regex or not response:
            return None
        matches = rule.regex.findall(response)
        if matches:
            preview = matches[:3]
            return f"Regex matched: {preview}"
        return None

    @staticmethod
    def _check_delegate(rule: Rule, response: str) -> str | None:
        if not response:
            return None
        has_code = bool(
            "```" in response or "def " in response or "class " in response
        )
        if has_code and "delegate" not in response.lower():
            return "Code detected without delegation keyword"
        return None

    @staticmethod
    def _check_gating(rule: Rule, reply_lower: str) -> str | None:
        """Check if response contains expected trigger name.
        This is a SOFT check — Gateway handles the hard gate.
        """
        import re as _re
        names = _re.findall(
            r'"([^"]+)"|«([^»]+)»|called\s+(\w+)|имени\s+(\w+)',
            rule.text, _re.IGNORECASE,
        )
        keywords = {w.lower() for group in names for w in group if w}
        if not keywords:
            words = rule.text.split()
            for w in reversed(words):
                w = w.strip('.,;:!?\"«»')
                if w and w[0].isupper() and len(w) >= 3:
                    keywords.add(w.lower())
                    break
        if keywords and not any(k in reply_lower for k in keywords):
            return (
                f"Response does not contain trigger name "
                f"({', '.join(sorted(keywords))})"
            )
        return None

    # ── Batch ──────────────────────────────────────────────────

    @classmethod
    def from_yaml_rules(cls, rules: list) -> "AdvancedRuleChecker":
        """Создать чекер из списка правил (строки или dict с priority/enforcement)."""
        checker = cls()
        for rule_spec in rules:
            checker.add_rule(rule_spec)
        return checker

    def parse_from_system_prompt(self, system_prompt: str) -> int:
        """Извлечь правила из [CRITICAL RULES] блока system_prompt."""
        if not system_prompt:
            return 0
        import re as _re
        match = _re.search(
            r"\[CRITICAL RULES\](.*?)(?:\[/CRITICAL RULES\]|\n\n(?:\[|These))",
            system_prompt, _re.DOTALL | _re.IGNORECASE,
        )
        if not match:
            return 0
        block = match.group(1).strip()
        rules_text = [
            _re.sub(r"^\d+\.\s*", "", line.strip())
            for line in block.splitlines()
            if line.strip() and not line.strip().startswith("These rules")
        ]
        rules_text = [r for r in rules_text if len(r) > 5]
        count = 0
        for rt in rules_text:
            self.add_rule(rt)
            count += 1
        return count

