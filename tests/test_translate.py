"""Translation backends: Apple's worker on macOS, a local model everywhere else."""
from __future__ import annotations

import unittest
from unittest import mock

from murmur import config, store, translate


class FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


def completion(text: str) -> FakeResponse:
    return FakeResponse(200, {"choices": [{"message": {"content": text}}]})


class HttpTranslatorTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.multiple(config, TRANSLATE_URL="", TRANSLATE_MODEL="local")
        patcher.start()
        self.addCleanup(patcher.stop)
        # Discovery is cached process-wide; keep each test starting from cold.
        cache = mock.patch.object(translate, "_discovery", None)
        cache.start()
        self.addCleanup(cache.stop)

    def test_no_endpoint_and_no_local_server_means_unavailable(self) -> None:
        with mock.patch("httpx.Client") as factory:
            factory.return_value.__enter__.return_value.get.side_effect = OSError("refused")
            self.assertFalse(translate.HttpTranslator().available())

    def test_a_running_local_server_is_discovered(self) -> None:
        models = mock.MagicMock(status_code=200)
        models.json.return_value = {"data": [{"id": "qwen3-4b"}]}
        with mock.patch("httpx.Client") as factory:
            client = factory.return_value.__enter__.return_value
            client.get.side_effect = [OSError("refused"), models]
            translator = translate.HttpTranslator()
            self.assertTrue(translator.available())
        self.assertEqual("http://127.0.0.1:8080/v1/chat/completions", translator.url)
        self.assertEqual("qwen3-4b", translator.model)

    def test_translation_uses_the_endpoint(self) -> None:
        translator = translate.HttpTranslator(url="http://127.0.0.1:8091/v1/chat/completions")
        self.assertTrue(translator.available())

        client = mock.MagicMock()
        client.post.return_value = completion("El zorro marrón salta.")
        with mock.patch("httpx.Client") as factory:
            factory.return_value.__enter__.return_value = client
            result = translator.translate("The brown fox jumps.", "es")

        self.assertEqual("El zorro marrón salta.", result["text"])
        self.assertTrue(result["translated"])
        sent = client.post.call_args.kwargs["json"]
        self.assertEqual("local", sent["model"])
        self.assertIn("Spanish", sent["messages"][0]["content"])
        self.assertEqual("The brown fox jumps.", sent["messages"][1]["content"])
        self.assertEqual(0, sent["temperature"])

    def test_quoted_answer_is_unwrapped(self) -> None:
        translator = translate.HttpTranslator(url="http://localhost/v1/chat/completions")
        client = mock.MagicMock()
        client.post.return_value = completion('"Le renard brun saute."')
        with mock.patch("httpx.Client") as factory:
            factory.return_value.__enter__.return_value = client
            result = translator.translate("The brown fox jumps.", "fr")
        self.assertEqual("Le renard brun saute.", result["text"])

    def test_server_error_becomes_a_translate_error(self) -> None:
        translator = translate.HttpTranslator(url="http://localhost/v1/chat/completions")
        client = mock.MagicMock()
        client.post.return_value = FakeResponse(503, {"error": "no model"})
        with mock.patch("httpx.Client") as factory:
            factory.return_value.__enter__.return_value = client
            with self.assertRaises(translate.TranslateError):
                translator.translate("Hello.", "es")

    def test_empty_answer_is_an_error(self) -> None:
        translator = translate.HttpTranslator(url="http://localhost/v1/chat/completions")
        client = mock.MagicMock()
        client.post.return_value = completion("   ")
        with mock.patch("httpx.Client") as factory:
            factory.return_value.__enter__.return_value = client
            with self.assertRaises(translate.TranslateError):
                translator.translate("Hello.", "es")

    def test_a_saved_base_url_is_posted_as_a_completion(self) -> None:
        # check_url() stores http://host/v1 - the endpoint a person connects in Settings
        # and the first-run screen. POSTing that string itself is a 404 from every
        # OpenAI-compatible server, so a connected server answered nothing at all.
        translator = translate.HttpTranslator(url="")
        client = mock.MagicMock()
        client.post.return_value = completion("El zorro marron salta.")
        with mock.patch.object(translate, "_saved_url", return_value="http://127.0.0.1:8091/v1"):
            with mock.patch("httpx.Client") as factory:
                factory.return_value.__enter__.return_value = client
                translator.translate("The brown fox jumps.", "es")
        self.assertEqual(
            "http://127.0.0.1:8091/v1/chat/completions", client.post.call_args.args[0]
        )

    def test_base_trailing_slash_and_full_urls_agree(self) -> None:
        for url in (
            "http://127.0.0.1:8091/v1",
            "http://127.0.0.1:8091/v1/",
            "http://127.0.0.1:8091/v1/chat/completions",
        ):
            with self.subTest(url=url):
                translator = translate.HttpTranslator(url=url)
                self.assertEqual(
                    "http://127.0.0.1:8091/v1/chat/completions", translator._endpoint(), url
                )


class ChainTests(unittest.TestCase):
    def test_apple_wins_when_it_exists(self) -> None:
        chain = translate.TranslatorChain()
        chain.swift = mock.MagicMock()
        chain.swift.available.return_value = True
        chain.swift.name = "apple"
        chain.swift.translate.return_value = {"text": "hola", "source": "en", "translated": True}
        chain.http = mock.MagicMock()
        chain.http.available.return_value = True
        chain.http.name = "http"

        self.assertEqual("apple", chain.backend())
        self.assertEqual("hola", chain.translate("hello", "es")["text"])
        chain.http.translate.assert_not_called()

    def test_falls_back_to_the_local_model(self) -> None:
        chain = translate.TranslatorChain()
        chain.swift = mock.MagicMock()
        chain.swift.available.return_value = False
        chain.http = mock.MagicMock()
        chain.http.available.return_value = True
        chain.http.name = "http"
        chain.http.translate.return_value = {"text": "hola", "source": "en", "translated": True}

        self.assertEqual("http", chain.backend())
        self.assertEqual("hola", chain.translate("hello", "es")["text"])

    def test_nothing_available_is_reported_not_silently_ignored(self) -> None:
        chain = translate.TranslatorChain()
        chain.swift = mock.MagicMock()
        chain.swift.available.return_value = False
        chain.http = mock.MagicMock()
        chain.http.available.return_value = False

        self.assertFalse(chain.available())
        with self.assertRaises(translate.TranslateUnavailable):
            chain.translate("hello", "es")


class MissingAdviceTests(unittest.TestCase):
    """The warning has to name the cause that is actually true on this platform."""

    def test_off_macos_points_at_the_local_endpoint(self) -> None:
        with mock.patch("sys.platform", "linux"):
            advice = translate.missing_advice()
        self.assertIn("MURMUR_TRANSLATE_URL", advice)
        self.assertNotIn("macOS", advice)

    def test_old_macos_says_apple_translation_needs_26(self) -> None:
        with mock.patch("sys.platform", "darwin"), mock.patch(
            "platform.mac_ver", return_value=("14.6.1", ("", "", ""), "")
        ):
            advice = translate.missing_advice()
        self.assertIn("macOS 26 or newer", advice)
        self.assertIn("14.6.1", advice)

    def test_macos_26_blames_the_build_not_the_reader(self) -> None:
        with mock.patch("sys.platform", "darwin"), mock.patch(
            "platform.mac_ver", return_value=("26.0", ("", "", ""), "")
        ):
            advice = translate.missing_advice()
        self.assertIn("no Apple translation helper", advice)
        self.assertIn("macOS 26 SDK", advice)

    def test_speak_reports_that_advice_instead_of_its_own_wording(self) -> None:
        from murmur import speak

        # Foreign text: the advice is only warranted when a translation was
        # actually needed (issue #41 - English into an English voice is silent).
        with mock.patch.object(
            translate, "translator", mock.Mock(available=mock.Mock(return_value=False))
        ), mock.patch("murmur.speak.store.translation_get", return_value=None), mock.patch(
            "murmur.speak.translate.missing_advice", return_value="advice"
        ):
            prepared = speak.prepare("El farero caminaba por la orilla.", "af_heart", enabled=True)
        self.assertEqual("advice", prepared["error"])
        self.assertEqual("El farero caminaba por la orilla.", prepared["text"])
        self.assertEqual("es", prepared["source"])


class PayloadCapTests(unittest.TestCase):
    def test_every_request_carries_a_token_cap(self) -> None:
        # Without a cap an unstable decode repeats one clause until the context ends -
        # measured on the local models, where a Japanese sentence spent the whole
        # 200-token budget repeating itself. A read must not hang on that.
        translator = translate.HttpTranslator(url="http://localhost/v1/chat/completions")
        client = mock.MagicMock()
        client.post.return_value = completion("hola")
        with mock.patch("httpx.Client") as factory:
            factory.return_value.__enter__.return_value = client
            translator.translate("Hello.", "es")
        payload = client.post.call_args.kwargs["json"]
        self.assertGreaterEqual(payload["max_tokens"], 256)
        self.assertLessEqual(payload["max_tokens"], 2048)

    def test_the_repetition_penalty_only_goes_to_this_apps_own_server(self) -> None:
        # llama.cpp understands the field; an OpenAI endpoint may reject an unknown one.
        for local, expected in ((True, True), (False, False)):
            with self.subTest(local=local):
                translator = translate.HttpTranslator(url="http://127.0.0.1:9/v1",
                                                      local=local, bound=True)
                client = mock.MagicMock()
                client.post.return_value = completion("hola")
                with mock.patch("httpx.Client") as factory:
                    factory.return_value.__enter__.return_value = client
                    translator.translate("Hello.", "es")
                payload = client.post.call_args.kwargs["json"]
                self.assertEqual(expected, "repeat_penalty" in payload)


class RoutingTests(unittest.TestCase):
    def _chain(self, *, local: bool = True, http: bool = True):
        chain = translate.TranslatorChain()
        chain.swift = mock.MagicMock(name="swift")
        chain.swift.available.return_value = False
        chain.http = mock.MagicMock(name="http")
        chain.http.name = "http"
        chain.http.url = ""
        chain.http.available.return_value = http
        chain.http.translate.return_value = {"text": "hola", "source": "en", "translated": True}
        chain.local = mock.MagicMock(name="local")
        chain.local.name = "local"
        chain.local.available.return_value = local
        chain.local.translate.return_value = {"text": "hola", "source": "en", "translated": True}
        return chain

    def test_a_language_routed_to_the_readers_server_uses_it(self) -> None:
        with mock.patch.object(store, "get_settings", return_value={"translateServerOnly": ["hi"]}):
            chain = self._chain()
            chain.translate("namaste", "hi")
        chain.http.translate.assert_called_once()
        chain.local.translate.assert_not_called()

    def test_everything_else_prefers_the_bundled_model(self) -> None:
        with mock.patch.object(store, "get_settings", return_value={"translateServerOnly": ["hi"]}):
            chain = self._chain()
            chain.translate("hello", "fr")
        chain.local.translate.assert_called_once()
        chain.http.translate.assert_not_called()

    def test_a_routed_language_falls_back_to_the_bundled_model_when_it_can_do_it(self) -> None:
        # Routing is a preference, not a promise: a language the bundled model handles goes
        # there when the server the reader routed it to is not running.
        with mock.patch.object(store, "get_settings",
                               return_value={"translateServerOnly": ["fr"]}):
            chain = self._chain(http=False)
            chain.translate("bonjour", "fr")
        chain.local.translate.assert_called_once()

    def test_a_local_failure_falls_back_to_the_readers_server(self) -> None:
        chain = self._chain()
        chain.local.translate.side_effect = translate.TranslateError("model died")
        result = chain.translate("hello", "fr")
        self.assertEqual("hola", result["text"])
        chain.http.translate.assert_called_once()

    def test_the_status_reports_the_local_model_and_the_routes(self) -> None:
        chain = self._chain(local=False)
        with mock.patch.object(store, "get_settings",
                               return_value={"translateServerOnly": ["hi"]}):
            status = chain.status()
        self.assertIn("local", status)
        self.assertEqual(["hi"], status["serverOnly"])
        self.assertIn("installed", status["local"])

    def test_the_bundled_model_refuses_a_language_it_cannot_do(self) -> None:
        # Hindi measured as fluent nonsense on every size (issue #150): refusing means the
        # original text is read with an accent, instead of gibberish being spoken.
        chain = self._chain(local=True, http=False)
        with mock.patch.object(translate.localmt, "supports", return_value=False):
            with self.assertRaises(translate.TranslateUnavailable) as caught:
                chain.translate("namaste", "hi")
        self.assertIn("Hindi", str(caught.exception))
        chain.local.translate.assert_not_called()

    def test_hindi_routed_to_a_server_still_uses_the_server(self) -> None:
        with mock.patch.object(store, "get_settings",
                               return_value={"translateServerOnly": ["hi"]}):
            chain = self._chain(local=True, http=True)
            with mock.patch.object(translate.localmt, "supports", return_value=False):
                chain.translate("namaste", "hi")
        chain.http.translate.assert_called_once()
        chain.local.translate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
