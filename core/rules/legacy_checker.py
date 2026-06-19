class RuleChecker:
    """Lightweight rule violation detector for agent responses.

    Uses keyword and pattern matching — zero LLM calls, instant.
    Designed to be called after every agent response to enforce
    critical rules from YAML configs.

    Usage::

        checker = RuleChecker(rules=[
            "Only respond when addressed as Grisha",
            "Never write code without explicit request",
        ])
        violations = checker.check("Here is a function: def foo(): ...")
        # → ["Never write code without explicit request"]
    """

    def __init__(self, rules: list[str] | None = None):
        self._rules: list[str] = list(rules) if rules else []

    @property
    def has_rules(self) -> bool:
        return len(self._rules) > 0

    def check(self, response: str) -> list[str]:
        """Check response against all rules. Returns list of violated rule texts."""
        if not self._rules or not response:
            return []

        violations: list[str] = []
        reply_lower = response.lower()

        for rule in self._rules:
            r = rule.lower()

            # ── Gating rules: "only respond when called X" ──────
            if ("отвечай только" in r or "respond only" in r or
                "only respond" in r):
                # Extract name from rule text — try multiple strategies
                import re
                names = re.findall(
                    r'"([^"]+)"|«([^»]+)»|called\s+(\w+)|имени\s+(\w+)|as\s+["\u201c]?(\w+)["\u201d]?',
                    rule, re.IGNORECASE,
                )
                keywords = {w.lower() for group in names for w in group if w}
                # Fallback: last word if it looks like a name (capitalised, 3+ chars)
                if not keywords:
                    words = rule.split()
                    for w in reversed(words):
                        w = w.strip('.,;:!?"\u201c\u201d')
                        if w and w[0].isupper() and len(w) >= 3:
                            keywords.add(w.lower())
                            break
                if keywords and not any(k in reply_lower for k in keywords):
                    violations.append(rule)

            # ── Code-without-request rules ───────────────────────
            if ("не пиши код" in r or "don't write code" in r or
                "do not write code" in r or "never write code" in r):
                has_code = bool(
                    "```" in response or
                    "def " in response or
                    "class " in response or
                    "import " in response or
                    "function " in response
                )
                if has_code:
                    violations.append(rule)

            # ── Terminal/execute restrictions ────────────────────
            if ("не используй terminal" in r or "не используй execute" in r or
                "don't use terminal" in r or "never run commands" in r):
                if ("execute_code" in response or "subprocess" in response or
                    "terminal(" in response):
                    violations.append(rule)

            # ── Delegation enforcement ───────────────────────────
            if "delegate" in r and "всегда" in r:
                if ("```" in response or "def " in response):
                    if "delegate" not in reply_lower:
                        violations.append(rule)

        return list(dict.fromkeys(violations))  # deduplicate

    def parse_from_system_prompt(self, system_prompt: str) -> int:
        """Extract rules from a system_prompt containing [CRITICAL RULES] block.

        Returns number of rules extracted.
        """
        if not system_prompt:
            return 0

        import re
        # Match [CRITICAL RULES] ... [/CRITICAL RULES] or just [CRITICAL RULES] ... end
        match = re.search(
            r'\[CRITICAL RULES\](.*?)(?:\[/CRITICAL RULES\]|$)',
            system_prompt, re.DOTALL | re.IGNORECASE,
        )
        if not match:
            return 0

        rules_text = match.group(1).strip()
        extracted = []

        for line in rules_text.split("\n"):
            line = line.strip()
            # Strip list markers: "1.", "2.", "- ", "* ", "•"
            for prefix in ("- ", "* ", "• "):
                if line.startswith(prefix):
                    line = line[len(prefix):].strip()
                    break
            # Strip numbered prefix: "1.", "2. "
            import re as re2
            line = re2.sub(r'^\d+\.\s*', '', line).strip()

            if line and len(line) > 5:  # skip empty/short lines
                extracted.append(line)

        self._rules = extracted
        return len(extracted)
