"""Адаптеры провайдеров модели. Пока один непростой: GigaChat.

ADR-0005 обещает, что смена провайдера — это строка конфигурации, а не
работа с кодом. Проверка этого обещания и породила файл: **обещание
выполняется не полностью.**

OpenAI-совместимый endpoint у GigaChat действительно есть, и запросы к нему
уходят без единой правки промптов — эта половина обещания держится. Не
держится вторая: доступ выдаётся не ключом, а токеном на тридцать минут,
который надо выменивать на авторизационные данные. Строки в `.env` для
этого мало, нужен код, который следит за сроком.

Отсюда правило на будущее: **«совместимый API» и «совместимый доступ» —
разные вещи.** Первое про формат запроса, второе про то, как в него попасть.

    AVTOGRAD_LLM_PROVIDER=gigachat
    GIGACHAT_AUTH_KEY=<Авторизационные данные из личного кабинета>
    GIGACHAT_SCOPE=GIGACHAT_API_PERS      для физлиц; _B2B и _CORP для прочих
    GIGACHAT_MODEL=GigaChat               GigaChat-Pro, GigaChat-Max

Сертификаты. Цепочка GigaChat подписана НУЦ Минцифры, которого нет в
стандартном хранилище Python. Путь к скачанному корневому сертификату
задаётся `GIGACHAT_CA_BUNDLE`. Отключить проверку целиком можно
`GIGACHAT_VERIFY=false`, но это именно отключение проверки подлинности
собеседника, а не настройка: переменная названа явно и о её действии
пишется в журнал при каждом запуске.
"""

from __future__ import annotations

import os
import time
import uuid

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
API_URL = "https://gigachat.devices.sberbank.ru/api/v1"

# Токен живёт полчаса. Обновляемся заранее: запрос, начатый за секунду до
# истечения, закончится уже после него и получит 401 на ровном месте.
EXPIRY_MARGIN_SECONDS = 120

_token: dict = {"value": "", "expires_at": 0.0}


def verify_setting():
    """Чему доверять при проверке сертификата сервера.

    Возврат: путь к связке, True (системное хранилище) или False
    (проверки нет). Третий случай сопровождается записью в журнал —
    молча ослаблять проверку подлинности нельзя.
    """
    bundle = os.environ.get("GIGACHAT_CA_BUNDLE", "").strip()
    if bundle:
        return bundle
    if os.environ.get("GIGACHAT_VERIFY", "").strip().lower() in ("0", "false", "no"):
        print("[gigachat] ВНИМАНИЕ: проверка сертификата отключена "
              "(GIGACHAT_VERIFY=false). Подлинность собеседника не подтверждается.")
        return False
    return True


def fetch_token(auth_key: str, scope: str, verify=True) -> tuple[str, float]:
    """Меняет авторизационные данные на access-токен.

    `RqUID` обязателен и должен быть новым на каждый запрос — сервер
    отвергает повторы. Возвращается сам токен и момент истечения.
    """
    import httpx

    with httpx.Client(timeout=30, verify=verify, trust_env=False) as client:
        response = client.post(
            OAUTH_URL,
            headers={
                "Authorization": f"Basic {auth_key}",
                "RqUID": str(uuid.uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={"scope": scope},
        )
    if response.status_code >= 400:
        raise RuntimeError(f"GigaChat не выдал токен: {response.status_code} "
                           f"{response.text[:200]}")
    body = response.json()
    token = body.get("access_token", "")
    if not token:
        raise RuntimeError(f"в ответе GigaChat нет access_token: {str(body)[:200]}")
    # Срок приходит в миллисекундах эпохи. Если поля нет — считаем полчаса.
    expires_at = float(body.get("expires_at", 0)) / 1000 or time.time() + 1800
    return token, expires_at


def access_token(force: bool = False) -> str:
    """Действующий токен: из памяти процесса либо свежий.

    Кэш на уровне модуля, а не клиента: обмен стоит запроса к чужому
    серверу, и делать его на каждую реплику клиента незачем.
    """
    auth_key = os.environ.get("GIGACHAT_AUTH_KEY", "").strip()
    if not auth_key:
        raise RuntimeError("GIGACHAT_AUTH_KEY не задан — подключиться не к чему")

    if not force and _token["value"] and time.time() < _token["expires_at"] - EXPIRY_MARGIN_SECONDS:
        return _token["value"]

    scope = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip()
    token, expires_at = fetch_token(auth_key, scope, verify=verify_setting())
    _token.update(value=token, expires_at=expires_at)
    left = max(0, int((expires_at - time.time()) / 60))
    print(f"[gigachat] токен получен, действует ещё {left} мин")
    return token


def gigachat_client():
    """Клиент OpenAI SDK, направленный на GigaChat со свежим токеном.

    Клиент создаётся заново на каждый вызов, а не хранится: токен встроен в
    заголовки при создании, и переживший истечение клиент молча получал бы
    401. Создание клиента дёшево, отладка такого 401 — нет.
    """
    import httpx
    from openai import OpenAI

    token = access_token()
    verify = verify_setting()
    return OpenAI(
        base_url=API_URL,
        api_key=token,
        http_client=httpx.Client(verify=verify, timeout=60, trust_env=False),
    )


def gigachat_model() -> str:
    return os.environ.get("GIGACHAT_MODEL", "GigaChat").strip() or "GigaChat"
