"""Tests for mail classification."""
from tools.secretary.mail_inbox import classify_mail, format_mail_list


def test_newsletter_noreply():
    assert classify_mail("Weekly Update", "noreply@company.com") == "newsletter"


def test_newsletter_unsubscribe():
    assert classify_mail("Deal", "deals@shop.com", "click here to unsubscribe") == "newsletter"


def test_important_keyword():
    assert classify_mail("Срочно: договор", "boss@gmail.com") == "important"
    assert classify_mail("Invoice #123", "client@company.com") == "important"


def test_personal_domain_important():
    assert classify_mail("Hi", "friend@gmail.com") == "important"
    assert classify_mail("Hello", "buddy@yandex.ru") == "important"


def test_other_default():
    assert classify_mail("Meeting notes", "coworker@company.com") == "other"


def test_format_with_labels():
    emails = [
        {"subject": "Urgent", "from_addr": "boss@gmail.com", "from_name": "Boss", "date_display": "10:00", "label": "important"},
        {"subject": "Weekly", "from_addr": "news@corp.com", "from_name": "News", "date_display": "09:00", "label": "newsletter"},
        {"subject": "Hi", "from_addr": "x@corp.com", "from_name": "X", "date_display": "08:00", "label": "other"},
    ]
    result = format_mail_list(emails)
    assert "Важное" in result
    assert "Рассылки" in result
    assert "Прочее" in result
    assert "Boss" in result
