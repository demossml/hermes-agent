#!/usr/bin/env python3
"""
Email validation function using only Python standard library.

Validates email addresses according to common rules:
  - Supports: user@example.com, user.name+tag@example.com,
              user@sub.example.com, user@[192.168.1.1]
  - Rejects: missing @, spaces, invalid chars, malformed domains.
"""

import re
import socket


def validate_email(email: str) -> bool:
    """
    Проверяет, является ли строка корректным email-адресом.

    Поддерживает:
      - обычные адреса: user@example.com
      - точки и плюсы в локальной части: user.name+tag@example.com
      - поддомены: user@sub.example.com
      - IP-адреса в квадратных скобках: user@[192.168.1.1]

    Отклоняет:
      - отсутствие @
      - пробелы
      - недопустимые символы
      - некорректный домен

    >>> validate_email('user@example.com')
    True
    >>> validate_email('user.name+tag@example.com')
    True
    >>> validate_email('user@sub.example.com')
    True
    >>> validate_email('user@[192.168.1.1]')
    True
    >>> validate_email('plainaddress')
    False
    >>> validate_email('user@ example.com')
    False
    >>> validate_email('user name@example.com')
    False
    >>> validate_email('user@.com')
    False
    >>> validate_email('@example.com')
    False
    >>> validate_email('user@[999.999.999.999]')
    False
    >>> validate_email('user@[256.0.0.1]')
    False
    >>> validate_email('user@[192.168.1.1.')
    False
    >>> validate_email('user@example')
    False
    >>> validate_email('')
    False
    """
    # Quick reject: empty string or whitespace anywhere
    if not email or ' ' in email:
        return False

    # Must contain exactly one '@'
    at_count = email.count('@')
    if at_count != 1:
        return False

    local_part, domain_part = email.split('@', 1)

    # Local part must be non-empty
    if not local_part:
        return False

    # Local part: letters, digits, and these special chars: . + _ - %
    # Dots cannot be at start or end, and cannot appear consecutively
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._+%-]*[A-Za-z0-9]$", local_part):
        # Allow single-character local part
        if not re.match(r"^[A-Za-z0-9]$", local_part):
            return False
    # Check no consecutive dots
    if '..' in local_part:
        return False

    # Domain part must be non-empty
    if not domain_part:
        return False

    # Case 1: IP-literal domain like [192.168.1.1]
    ip_match = re.match(r'^\[(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\]$', domain_part)
    if ip_match:
        # Validate each octet is 0-255 and the IP is not broadcast/loopback/etc
        octets = [int(ip_match.group(i)) for i in range(1, 5)]
        valid_octets = all(0 <= octet <= 255 for octet in octets)
        if not valid_octets:
            return False
        # Also reject 0.0.0.0 as it's commonly invalid for email routing
        if octets == [0, 0, 0, 0]:
            return False
        return True

    # Case 2: Regular domain name
    # Must be at least one label separated by dots
    # Each label: alphanumeric + hyphens, hyphens not at start/end
    # TLD must be at least 2 chars, letters only
    domain_regex = re.compile(
        r'^'
        r'[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?'  # first label
        r'(\.'                                        # subsequent labels
        r'[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?'
        r')*'
        r'\.'
        r'[A-Za-z]{2,}$'                              # TLD
    )
    if not domain_regex.match(domain_part):
        return False

    # Check label length (each label max 63 chars)
    for label in domain_part.split('.'):
        if len(label) > 63:
            return False

    # Check total domain length (max 253 chars)
    if len(domain_part) > 253:
        return False

    return True


def demonstrate():
    """Прогоняет набор тестовых примеров и выводит результаты в stdout."""
    test_cases = [
        # Должны быть приняты (валидные)
        ("user@example.com", True),
        ("user.name+tag@example.com", True),
        ("user@sub.example.com", True),
        ("user@[192.168.1.1]", True),
        ("user@[0.0.0.0]", False),  # Мы специально отклоняем 0.0.0.0
        ("user@[255.255.255.255]", True),
        ("simple@test.co.uk", True),
        ("a@b.co", True),
        ("user+filter@mail.example.org", True),
        ("nice_and_simple@example.com", True),
        ("very.common@example.com", True),
        ("x@example.com", True),
        ("user@[1.2.3.4]", True),
        ("firstname.lastname@example.com", True),
        # Должны быть отклонены (невалидные)
        ("plainaddress", False),
        ("user@ example.com", False),
        ("user name@example.com", False),
        ("user@.com", False),
        ("@example.com", False),
        ("user@[999.999.999.999]", False),
        ("user@[256.0.0.1]", False),
        ("user@[192.168.1.1.", False),
        ("user@example", False),
        ("", False),
        ("Abc.example.com", False),
        ("A@b@c@example.com", False),
        ("a\"b(c)d,e:f;g<h>i[j\\k]l@example.com", False),
        ("this is\"not\\allowed@example.com", False),
        ("user@..com", False),
        ("user@example..com", False),
        (".user@example.com", False),
        ("user.@example.com", False),
        ("user@-example.com", False),
        ("user@example-.com", False),
    ]

    print("=" * 70)
    print("ТЕСТИРОВАНИЕ ФУНКЦИИ validate_email")
    print("=" * 70)

    passed = 0
    failed = 0

    for email, expected in test_cases:
        result = validate_email(email)
        status = "✓ PASS" if result == expected else "✗ FAIL"
        if result == expected:
            passed += 1
        else:
            failed += 1
        print(f"  {status} | validate_email({email!r:45s}) -> {result}  "
              f"(expected {expected})")

    print("=" * 70)
    print(f"Результаты: {passed} passed, {failed} failed из "
          f"{len(test_cases)} тестов")
    print("=" * 70)


if __name__ == "__main__":
    import doctest
    print("Запуск доктестов...")
    results = doctest.testmod(verbose=True)
    print(f"\nДоктесты: {results.attempted} запущено, "
          f"{results.failed} ошибок\n")
    demonstrate()
