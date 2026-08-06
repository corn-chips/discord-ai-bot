"""The `safety:` block used to fail open, including for two real SDK names.

`_generate_response_async` resolved every configured threshold through a
four-entry dict with `.get(value, BLOCK_NONE)`. One of its four keys,
`BLOCK_HIGH_AND_ABOVE`, is not a name the SDK has ever defined, and everything
the dict did not know answered `BLOCK_NONE` -- so an operator asking for
filtering got none, silently, on every request:

    BLOCK_ONLY_HIGH                  -> BLOCK_NONE
    OFF                              -> BLOCK_NONE
    HARM_BLOCK_THRESHOLD_UNSPECIFIED -> BLOCK_NONE
    block_medium_and_above           -> BLOCK_NONE

The first two are genuine members of `types.HarmBlockThreshold`, which is why
failing closed would not on its own have been a fix: it would have turned a
silently permissive correct config into a loudly broken one.

These assert the threshold that reaches `types.GenerateContentConfig`, read out
of the object handed to the SDK rather than off the resolver, because that is
what the provider acts on. The shipped `BLOCK_NONE` default is pinned too,
deliberately: it is a product decision about what this bot is for, and it must
not drift on the back of a bug fix in the map underneath it.

**Two kinds of test live here and they are not interchangeable.** Most are
defect guards: they fail on the pre-fix code, and `scripts/mutants.toml` proves
each one still would. Five are not, and cannot be:

    test_the_shipped_default_still_sends_block_none
    test_the_legacy_alias_still_reaches_the_sdk_name_it_meant
    test_the_shipped_defaults_validate
    test_every_real_sdk_name_validates
    test_the_legacy_alias_validates

Measured, not assumed -- the pre-fix resolver (the four-entry map above) and the
absence of any safety validation were reintroduced in a scratch tree and this
file was run against them. The first two pass because `BLOCK_NONE` and
`BLOCK_HIGH_AND_ABOVE` were both keys in that map; the last three pass because
before `071f442` nothing validated the `safety:` block at all, so every config
"validated cleanly" vacuously.

That is what they are for, not a weakness in them. Failing closed would not have
been a fix on its own -- with `BLOCK_ONLY_HIGH` still unrecognised, a correct
config would merely have gone from silently permissive to loudly broken -- so
the repair had to be proved harmless to configs that already worked, and a test
that proves that is a test the unfixed code passes by construction. They guard
everything *after* the fix rather than the fix itself. Deleting them because
they cannot fail on the defect would delete the only evidence that the defect
was closed without collateral.
"""

import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from google.genai import types

from src.config import BotConfig
from src.config_helpers import canonical_safety_threshold
from src.constants import (
    SAFETY_THRESHOLD_ALIASES,
    SAFETY_THRESHOLD_FALLBACK,
    SAFETY_THRESHOLD_NAMES,
)
from src.services import gemini_client
from src.services.gemini_client import GeminiClient


class SafetyThresholdVocabularyTest(unittest.TestCase):
    """What the SDK actually defines, as opposed to what this repo believed."""

    def test_the_declared_names_are_exactly_the_sdk_enum(self):
        # The drift guard. The names are declared in src/constants.py rather
        # than read off the SDK, because config has to validate before any
        # provider client exists -- so a future SDK adding or renaming a member
        # has to fail here rather than quietly diverge.
        self.assertEqual(
            set(SAFETY_THRESHOLD_NAMES),
            {member.name for member in types.HarmBlockThreshold},
        )

    def test_the_alias_is_not_an_sdk_name_and_points_at_one(self):
        # Why the alias table exists: BLOCK_HIGH_AND_ABOVE was invented here,
        # and config.yaml's own comment advertised it while listing neither
        # BLOCK_ONLY_HIGH nor OFF -- so it is the name an operator following
        # the shipped documentation would have written.
        #
        # Named explicitly rather than derived from the table. Iterating the
        # table would pass over an empty one, which is exactly the change this
        # is here to catch.
        self.assertEqual(
            SAFETY_THRESHOLD_ALIASES, {"BLOCK_HIGH_AND_ABOVE": "BLOCK_ONLY_HIGH"}
        )
        sdk_names = {m.name for m in types.HarmBlockThreshold}
        for invented, real in SAFETY_THRESHOLD_ALIASES.items():
            with self.subTest(alias=invented):
                self.assertNotIn(invented, sdk_names)
                self.assertIn(real, sdk_names)

    def test_the_fallback_names_a_threshold_the_sdk_defines(self):
        # This was called ...is_the_strictest_threshold_the_sdk_offers and
        # asserted a literal, which is not that property and cannot be made
        # into it: `types.HarmBlockThreshold` is a str enum whose values are
        # its own names, so there is no ordinal to compare and no ordering the
        # SDK exposes. Declaration order does not stand in for one either --
        # HARM_BLOCK_THRESHOLD_UNSPECIFIED is first, and it is the proto zero
        # value rather than the strictest policy.
        #
        # What is assertable here is that the fallback resolves at all:
        # `resolve_safety_threshold` ends in types.HarmBlockThreshold[name],
        # which raises KeyError on a name the SDK does not define -- turning an
        # already-degraded path into a crash on every request. WHICH threshold
        # it is has moved to test_an_unresolvable_threshold_does_not_disable_
        # the_filter, where it is asserted on the request payload instead of on
        # the constant that produced it.
        self.assertIn(SAFETY_THRESHOLD_FALLBACK, SAFETY_THRESHOLD_NAMES)
        self.assertEqual(
            types.HarmBlockThreshold[SAFETY_THRESHOLD_FALLBACK].name,
            SAFETY_THRESHOLD_FALLBACK,
        )

    def test_case_and_surrounding_space_do_not_change_the_meaning(self):
        self.assertEqual(canonical_safety_threshold("  block_only_high "), "BLOCK_ONLY_HIGH")

    def test_a_near_miss_resolves_to_nothing_rather_than_to_permissive(self):
        for value in ("BLOCK_MEDIUM", "BLOCK_HIGH", "none", "", None, 7):
            with self.subTest(value=value):
                self.assertEqual(canonical_safety_threshold(value), "")


class SafetyThresholdRequestTest(unittest.IsolatedAsyncioTestCase):
    """What is actually sent to Gemini for a given `safety:` block."""

    async def _thresholds_sent(self, value):
        """Drive the real request builder and read back what it configured."""

        client = object.__new__(GeminiClient)
        client.config = SimpleNamespace(
            temperature=0.7,
            top_p=0.8,
            top_k=40,
            max_output_tokens_low=100,
            max_output_tokens_medium=200,
            max_output_tokens_high=300,
            safety_harassment=value,
            safety_hate_speech=value,
            safety_sexually_explicit=value,
            safety_dangerous_content=value,
            model_thinking_backend={"runtime-model": "none"},
        )
        client._current_model_name = "runtime-model"
        client._current_complexity_level = "medium"
        captured = {}

        async def generate_content(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(text="ok", candidates=[], usage_metadata=None)

        client.client = SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
        )

        await client._generate_response_async("hello")

        settings = captured["config"].safety_settings
        return {
            setting.category.name: setting.threshold.name for setting in settings
        }

    async def test_every_real_sdk_name_survives_to_the_request(self):
        # The headline. Three of these six used to arrive as BLOCK_NONE.
        for name in sorted(SAFETY_THRESHOLD_NAMES):
            with self.subTest(threshold=name):
                sent = await self._thresholds_sent(name)
                self.assertEqual(set(sent.values()), {name})

    async def test_the_shipped_default_still_sends_block_none(self):
        # Pinned on purpose. Fixing the map must not tighten the default: what
        # this bot filters is a product decision, taken in config.yaml and in
        # the system prompts, and it is not one a lookup-table repair makes.
        #
        # config.yaml is the shipped default -- the dataclass field is only the
        # value used when the key is absent, and asserting that alone left
        # every one of these four editable in config.yaml with the suite green.
        shipped = yaml.safe_load(
            (Path(__file__).resolve().parent.parent / "config.yaml").read_text()
        )["safety"]
        self.assertEqual(
            shipped,
            {
                "harassment": "BLOCK_NONE",
                "hate_speech": "BLOCK_NONE",
                "sexually_explicit": "BLOCK_NONE",
                "dangerous_content": "BLOCK_NONE",
            },
        )
        self.assertEqual(BotConfig.safety_harassment, "BLOCK_NONE")
        self.assertEqual(BotConfig.safety_hate_speech, "BLOCK_NONE")
        self.assertEqual(BotConfig.safety_sexually_explicit, "BLOCK_NONE")
        self.assertEqual(BotConfig.safety_dangerous_content, "BLOCK_NONE")

        sent = await self._thresholds_sent("BLOCK_NONE")

        self.assertEqual(set(sent.values()), {"BLOCK_NONE"})

    async def test_the_legacy_alias_still_reaches_the_sdk_name_it_meant(self):
        # A config written against the old map must keep working.
        sent = await self._thresholds_sent("BLOCK_HIGH_AND_ABOVE")

        self.assertEqual(set(sent.values()), {"BLOCK_ONLY_HIGH"})

    async def test_an_unresolvable_threshold_does_not_disable_the_filter(self):
        # The direction of the fallback is the whole point: an operator who
        # mistyped a threshold was asking for more filtering, not for none.
        #
        # The literal is asserted here rather than beside the constant, and
        # against the request payload rather than against
        # SAFETY_THRESHOLD_FALLBACK, for two reasons. Comparing to the constant
        # would pass for any value of it, including OFF -- equally permissive
        # as BLOCK_NONE and not excluded by a `not BLOCK_NONE` check. And with
        # the literal beside the constant, M-DAB052B took two tests down at
        # once, the second of which observed nothing but the constant's own
        # text. There is one killer now, and it is the behaviour.
        with self.assertLogs("src.services.gemini_client", "ERROR"):
            sent = await self._thresholds_sent("BLOCK_MEDIUM")

        self.assertEqual(set(sent.values()), {"BLOCK_LOW_AND_ABOVE"})

    async def test_the_streaming_path_sends_the_same_thresholds(self):
        # Both branches share one GenerateContentConfig today, so this guards
        # against a future split rather than a second defect: the two request
        # builders must not be allowed to drift apart on safety.
        client = object.__new__(GeminiClient)
        client.config = SimpleNamespace(
            temperature=0.7,
            top_p=0.8,
            top_k=40,
            max_output_tokens_low=100,
            max_output_tokens_medium=200,
            max_output_tokens_high=300,
            safety_harassment="BLOCK_ONLY_HIGH",
            safety_hate_speech="BLOCK_ONLY_HIGH",
            safety_sexually_explicit="BLOCK_ONLY_HIGH",
            safety_dangerous_content="BLOCK_ONLY_HIGH",
            model_thinking_backend={"runtime-model": "none"},
        )
        client._current_model_name = "runtime-model"
        client._current_complexity_level = "medium"
        captured = {}

        async def generate_content_stream(**kwargs):
            captured.update(kwargs)

            async def chunks():
                yield SimpleNamespace(text="ok", candidates=[], usage_metadata=None)

            return chunks()

        client.client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_content_stream=generate_content_stream)
            )
        )

        async def on_chunk(_text):
            return None

        await client._generate_response_async("hello", on_chunk=on_chunk)

        self.assertEqual(
            {s.threshold.name for s in captured["config"].safety_settings},
            {"BLOCK_ONLY_HIGH"},
        )

    async def test_a_yaml_boolean_off_is_read_as_the_threshold_it_spells(self):
        # `dangerous_content: OFF` unquoted is a YAML 1.1 boolean, so what
        # arrives here is False. Every spelling that produces it -- OFF, no,
        # false -- means the same threshold, and reporting `got False` beside
        # an error message listing OFF as valid would be a puzzle, not a fix.
        sent = await self._thresholds_sent(False)

        self.assertEqual(set(sent.values()), {"OFF"})

    async def test_an_sdk_enum_member_is_accepted_as_itself(self):
        # A BotConfig built in code can hold the member rather than its name.
        # It is a str subclass, but str() on it gives
        # 'HarmBlockThreshold.BLOCK_ONLY_HIGH', so it has to be used directly.
        sent = await self._thresholds_sent(types.HarmBlockThreshold.BLOCK_ONLY_HIGH)

        self.assertEqual(set(sent.values()), {"BLOCK_ONLY_HIGH"})

    async def test_the_four_categories_are_resolved_independently(self):
        client = object.__new__(GeminiClient)
        client.config = SimpleNamespace(
            temperature=0.7,
            top_p=0.8,
            top_k=40,
            max_output_tokens_low=100,
            max_output_tokens_medium=200,
            max_output_tokens_high=300,
            safety_harassment="BLOCK_NONE",
            safety_hate_speech="BLOCK_LOW_AND_ABOVE",
            safety_sexually_explicit="BLOCK_MEDIUM_AND_ABOVE",
            safety_dangerous_content="BLOCK_ONLY_HIGH",
            model_thinking_backend={"runtime-model": "none"},
        )
        client._current_model_name = "runtime-model"
        client._current_complexity_level = "medium"
        captured = {}

        async def generate_content(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(text="ok", candidates=[], usage_metadata=None)

        client.client = SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
        )

        await client._generate_response_async("hello")

        self.assertEqual(
            {s.category.name: s.threshold.name for s in captured["config"].safety_settings},
            {
                "HARM_CATEGORY_HARASSMENT": "BLOCK_NONE",
                "HARM_CATEGORY_HATE_SPEECH": "BLOCK_LOW_AND_ABOVE",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_MEDIUM_AND_ABOVE",
                "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_ONLY_HIGH",
            },
        )


class SafetyThresholdValidationTest(unittest.TestCase):
    """A bad threshold is a boot error, not a per-request surprise."""

    @staticmethod
    def _config(**overrides):
        model_name = "test-model"
        return BotConfig(
            discord_token="d" * 50,
            gemini_api_key="g" * 30,
            default_model=model_name,
            router_model_name=model_name,
            available_models=[{"name": "Test Model", "value": model_name}],
            valid_models=[model_name],
            model_thinking_backend={model_name: "thinking_level"},
            model_complexity={
                "low": {"model": model_name, "thinking_level": "minimal"},
                "medium": {"model": model_name, "thinking_level": "low"},
                "high": {"model": model_name, "thinking_level": "high"},
            },
            nano_banana_model="test-image-model",
            system_prompt_high_complexity="high prompt",
            system_prompt_low_complexity="low prompt",
            system_prompt_medium_complexity="medium prompt",
            **overrides,
        )

    def test_the_shipped_defaults_validate(self):
        self.assertEqual(self._config().validate(), [])

    def test_every_real_sdk_name_validates(self):
        for name in sorted(SAFETY_THRESHOLD_NAMES):
            with self.subTest(threshold=name):
                config = self._config(safety_harassment=name)
                self.assertEqual(config.validate(), [])

    def test_the_legacy_alias_validates(self):
        self.assertEqual(self._config(safety_harassment="BLOCK_HIGH_AND_ABOVE").validate(), [])

    def test_each_of_the_four_settings_is_checked(self):
        for setting in (
            "safety_harassment",
            "safety_hate_speech",
            "safety_sexually_explicit",
            "safety_dangerous_content",
        ):
            with self.subTest(setting=setting):
                errors = self._config(**{setting: "BLOCK_MEDIUM"}).validate()
                self.assertEqual(len(errors), 1, errors)
                self.assertIn(f"safety.{setting[len('safety_'):]}", errors[0])

    def test_the_error_names_the_values_that_would_work(self):
        errors = self._config(safety_harassment="BLOCK_MEDIUM").validate()

        self.assertEqual(len(errors), 1)
        self.assertIn("BLOCK_MEDIUM_AND_ABOVE", errors[0])
        self.assertIn("BLOCK_ONLY_HIGH", errors[0])
        self.assertIn("'BLOCK_MEDIUM'", errors[0])


if __name__ == "__main__":
    unittest.main()


class SafetyThresholdLogVolumeTest(unittest.TestCase):
    """One unrecognised value costs one ERROR line, not one per category per call.

    `resolve_safety_threshold` runs for all four harm categories on every
    request config that carries safety settings, so a single bad threshold
    produced four identical ERROR records per message. The volume is the whole
    defect: the fallback itself is correct, and it does not fire for an operator
    who has booted normally -- the shipped config canonicalises cleanly on all
    four categories, so reaching it means a `BotConfig` got to the client
    without passing `validate_config`.
    """

    def setUp(self):
        gemini_client._reported_unknown_thresholds.clear()
        self.addCleanup(gemini_client._reported_unknown_thresholds.clear)

    def test_one_bad_value_is_reported_once_however_often_it_is_resolved(self):
        with self.assertLogs("src.services.gemini_client", "ERROR") as captured:
            for _ in range(4):
                gemini_client.resolve_safety_threshold("BLOCK_EVERYTHING")

        self.assertEqual(len(captured.records), 1)

    def test_a_second_distinct_bad_value_is_still_reported(self):
        # Deduplication must not swallow a different misconfiguration.
        with self.assertLogs("src.services.gemini_client", "ERROR") as captured:
            gemini_client.resolve_safety_threshold("BLOCK_EVERYTHING")
            gemini_client.resolve_safety_threshold("BLOCK_NOTHING_AT_ALL")

        self.assertEqual(len(captured.records), 2)

    def test_the_fallback_is_unchanged_by_the_deduplication(self):
        # Quieter must not mean more permissive: every call still resolves to
        # the strict fallback, including the ones that log nothing.
        with self.assertLogs("src.services.gemini_client", "ERROR"):
            first = gemini_client.resolve_safety_threshold("BLOCK_EVERYTHING")
        second = gemini_client.resolve_safety_threshold("BLOCK_EVERYTHING")

        expected = types.HarmBlockThreshold[gemini_client.SAFETY_THRESHOLD_FALLBACK]
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
