"""Тесты адаптера GigaChat: обмен токена, срок жизни, проверка сертификата.

Ключей GigaChat здесь нет и не нужно: обмен подменяется заглушкой. Проверяется
то, что ломается молча, — истёкший токен и отключённая проверка подлинности.

Запуск:  python tests/test_providers.py
Код возврата 1 — есть падения.
"""

from __future__ import annotations

import io
import contextlib
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import providers  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

passed, failed = 0, []


def check(condition: bool, title: str) -> None:
    global passed
    if condition:
        passed += 1
    else:
        failed.append(title)


def reset(**env) -> None:
    """Чистое окружение на каждый тест: кэш токена живёт в модуле."""
    providers._token.update(value="", expires_at=0.0)
    for key in ("GIGACHAT_AUTH_KEY", "GIGACHAT_SCOPE", "GIGACHAT_CA_BUNDLE",
                "GIGACHAT_VERIFY", "GIGACHAT_MODEL"):
        os.environ.pop(key, None)
    os.environ.update(env)


class Exchange:
    """Заглушка обмена: считает вызовы и отдаёт заданный срок жизни."""

    def __init__(self, minutes: float = 30):
        self.calls = 0
        self.minutes = minutes
        self.scopes: list[str] = []

    def __call__(self, auth_key, scope, verify=True):
        self.calls += 1
        self.scopes.append(scope)
        return f"token-{self.calls}", time.time() + self.minutes * 60


def with_exchange(exchange):
    providers.fetch_token = exchange


def test_token_is_reused() -> None:
    """Обмен стоит запроса к чужому серверу — на каждую реплику его не делают."""
    reset(GIGACHAT_AUTH_KEY="key")
    exchange = Exchange(minutes=30)
    with_exchange(exchange)

    with contextlib.redirect_stdout(io.StringIO()):
        first = providers.access_token()
        second = providers.access_token()

    check(first == second == "token-1", "второй вызов берёт токен из памяти")
    check(exchange.calls == 1, "обмен выполнен один раз")
    check(exchange.scopes == ["GIGACHAT_API_PERS"], "по умолчанию scope для физлиц")


def test_token_refreshed_before_expiry() -> None:
    """Запрос, начатый за секунду до истечения, закончится уже после него."""
    reset(GIGACHAT_AUTH_KEY="key")
    exchange = Exchange(minutes=1)  # меньше запаса в две минуты
    with_exchange(exchange)

    with contextlib.redirect_stdout(io.StringIO()):
        providers.access_token()
        providers.access_token()

    check(exchange.calls == 2, "токен с остатком меньше запаса обновляется заранее")


def test_force_refresh() -> None:
    reset(GIGACHAT_AUTH_KEY="key")
    exchange = Exchange(minutes=30)
    with_exchange(exchange)
    with contextlib.redirect_stdout(io.StringIO()):
        providers.access_token()
        forced = providers.access_token(force=True)
    check(forced == "token-2", "принудительное обновление берёт новый токен")


def test_scope_and_model_configurable() -> None:
    reset(GIGACHAT_AUTH_KEY="key", GIGACHAT_SCOPE="GIGACHAT_API_CORP",
          GIGACHAT_MODEL="GigaChat-Pro")
    exchange = Exchange()
    with_exchange(exchange)
    with contextlib.redirect_stdout(io.StringIO()):
        providers.access_token()
    check(exchange.scopes == ["GIGACHAT_API_CORP"], "scope берётся из окружения")
    check(providers.gigachat_model() == "GigaChat-Pro", "модель берётся из окружения")

    reset(GIGACHAT_AUTH_KEY="key")
    check(providers.gigachat_model() == "GigaChat", "по умолчанию базовая модель")


def test_missing_key_says_so() -> None:
    reset()
    try:
        providers.access_token()
        check(False, "без ключа обмен не выполняется")
    except RuntimeError as exc:
        check("GIGACHAT_AUTH_KEY" in str(exc), "без ключа названа причина")


def test_verify_defaults_to_on() -> None:
    """Проверка подлинности не должна отключаться молча."""
    reset()
    check(providers.verify_setting() is True, "по умолчанию сертификат проверяется")

    reset(GIGACHAT_CA_BUNDLE="/путь/к/russian_trusted_root_ca.cer")
    check(providers.verify_setting() == "/путь/к/russian_trusted_root_ca.cer",
          "указанная связка используется вместо системной")

    reset(GIGACHAT_VERIFY="false")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        setting = providers.verify_setting()
    check(setting is False, "проверку можно отключить явной переменной")
    check("ВНИМАНИЕ" in out.getvalue(), "об отключении пишется в журнал")
    check("подлинность" in out.getvalue().lower(),
          "сказано, что именно перестало проверяться")


def test_core_picks_provider_by_name() -> None:
    """Имя провайдера в окружении меняет и клиента, и модель по умолчанию."""
    reset(GIGACHAT_AUTH_KEY="key", GIGACHAT_MODEL="GigaChat-Max")
    os.environ["AVTOGRAD_LLM_PROVIDER"] = "gigachat"
    os.environ["AVTOGRAD_MODEL"] = "gpt-5-mini"
    try:
        import assistant
        check(assistant.default_model() == "GigaChat-Max",
              "модель OpenAI не уезжает в GigaChat вместе с провайдером")
    finally:
        os.environ.pop("AVTOGRAD_LLM_PROVIDER", None)
        os.environ.pop("AVTOGRAD_MODEL", None)

    import assistant
    check(assistant.default_model() == "gpt-5-mini",
          "без имени провайдера поведение прежнее")


def main() -> int:
    for test in (test_token_is_reused, test_token_refreshed_before_expiry,
                 test_force_refresh, test_scope_and_model_configurable,
                 test_missing_key_says_so, test_verify_defaults_to_on,
                 test_core_picks_provider_by_name):
        test()
    print(f"Проверок выполнено: {passed + len(failed)}")
    if failed:
        print(f"\nПадений: {len(failed)}")
        for title in failed:
            print(f"  · {title}")
        return 1
    print("Падений нет. Токен обновляется вовремя, проверка сертификата не молчит.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
