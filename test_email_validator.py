
import pytest
from email_validator import is_valid_email


# ---- Валидные email'ы ----
@pytest.mark.parametrize("email", [
    "user@example.com",
    "user.name@example.com",
    "user+tag@example.com",
    "user_name@example.com",
    "user%name@example.com",
    "123@example.com",
    "a@b.cd",
    "prettyandsimple@example.com",
    "very.common@example.com",
    "disposable.style.email.with+symbol@example.com",
    "other.email-with-hyphen@example.com",
    "x@example.com",
    "example-indeed@strange-example.com",
    "admin@mailserver1",
    "user@[192.168.1.1]",
    "user@[0.0.0.0]",
    "user@[255.255.255.255]",
    # Юникод
    "пользователь@example.com",
    "用户@example.com",
    "用戶@example.com",
])
def test_valid_emails(email: str):
    """Валидные email-адреса должны возвращать True."""
    assert is_valid_email(email), f"Ожидался True для {email!r}"


# ---- Невалидные email'ы ----
@pytest.mark.parametrize("email", [
    "",                    # Пустая строка
    " ",                   # Пробел
    "user",                # Нет @
    "@example.com",        # Пустая локальная часть
    "user@",               # Пустая доменная часть
    "user@@example.com",   # Два @
    "user@exa mple.com",   # Пробел в домене
    ".user@example.com",   # Точка в начале локальной части
    "user.@example.com",   # Точка в конце локальной части
    "user..name@example.com",  # Две точки подряд в локальной части
    "user@.example.com",   # Точка в начале домена
    "user@example.com.",   # Точка в конце домена
    "user@example..com",   # Две точки подряд в домене
    "user@example",        # Только один сегмент домена (без точки)
    "a" * 65 + "@example.com",  # Локальная часть > 64 символов
    "user@" + "x" * 256,   # Домен > 255 символов
    "user@exa" + "m" * 63 + ".com",  # Сегмент домена > 63 символов
    "user name@example.com",  # Пробел в локальной части
    "user!name@example.com",  # Недопустимый спецсимвол
    "user#name@example.com",  # Недопустимый спецсимвол
    "user@[256.0.0.1]",    # Октет > 255
    "user@[192.168.1]",    # Неполный IP
    "user@[192.168.1.1",   # Незакрытая скобка
    "user@192.168.1.1",    # IP без скобок
    None,                  # None
    123,                   # Число, не строка
])
def test_invalid_emails(email):
    """Невалидные email-адреса должны возвращать False."""
    assert not is_valid_email(email), f"Ожидался False для {email!r}"
