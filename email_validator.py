
import re


def is_valid_email(email: str) -> bool:
    """Проверяет, является ли строка валидным email-адресом.

    Валидация соответствует RFC 5321/5322 с разумными упрощениями:
    - Ровно один символ @
    - Локальная часть: 1-64 символа, буквы (вкл. юникод), цифры, . _ % + -
    - Доменная часть: 2-255 символов, минимум два сегмента через точку,
      каждый сегмент не длиннее 63 символов
    - Точка не может быть первой/последней или идти подряд в обеих частях
    - IP-адрес в квадратных скобках [x.x.x.x] — валидный домен
    """
    if not isinstance(email, str):
        return False

    if not email or len(email) > 254:
        return False

    # Ровно один @
    at_count = email.count("@")
    if at_count != 1:
        return False

    local, domain = email.rsplit("@", 1)

    # --- Локальная часть ---
    if not local or len(local) > 64:
        return False

    # Точка не первой и не последней
    if local.startswith(".") or local.endswith("."):
        return False

    # Две точки подряд
    if ".." in local:
        return False

    # Разрешённые символы в локальной части
    # Латинские буквы, цифры, юникодные буквы/цифры, . _ % + -
    local_pattern = re.compile(r"^[\w._%+\-]+$", re.UNICODE)
    if not local_pattern.match(local):
        return False

    # --- Доменная часть ---
    if not domain or len(domain) > 255:
        return False

    # IP-адрес в скобках [192.168.1.1]
    if domain.startswith("[") and domain.endswith("]"):
        ip_content = domain[1:-1]
        ip_pattern = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
        if not ip_pattern.match(ip_content):
            return False
        # Проверка октетов
        for octet in ip_content.split("."):
            if not 0 <= int(octet) <= 255:
                return False
        return True

    # Обычный домен — разбиваем на сегменты по точке
    if domain.startswith(".") or domain.endswith("."):
        return False

    if ".." in domain:
        return False

    segments = domain.split(".")
    if len(segments) < 2:
        # Односегментный домен (admin@mailserver1) — валиден для локальных сетей
        seg = segments[0]
        if not seg or len(seg) > 63:
            return False
        if not re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$", seg):
            return False
        return True

    for seg in segments:
        if not seg or len(seg) > 63:
            return False
        # Каждый сегмент: буквы, цифры, дефис (не первый и не последний)
        if not re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$", seg):
            return False

    # IP-адрес без скобок невалиден — если все сегменты состоят только из цифр
    # и их 4 (формат a.b.c.d), это скорее всего голый IP, а не домен
    if len(segments) == 4 and all(seg.isdigit() for seg in segments):
        return False

    return True
