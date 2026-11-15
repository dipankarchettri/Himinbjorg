"""Confidential mode.

Resolved once at startup and frozen. The guarantee is structural rather
than behavioural: in confidential mode the unsafe options are absent from
the object graph, so no code path can select one.

The egress assertion here is the last chokepoint before bytes leave the
process. It raises; it does not return a boolean, because a boolean can
be ignored.
"""
from __future__ import annotations

from dataclasses import dataclass

LOCAL_PREFIX = "ollama/"


class PrivacyViolation(RuntimeError):
    """Raised at the egress chokepoint. Never caught and continued from."""


@dataclass(frozen=True)
class PrivacyMode:
    confidential: bool
    virtual_key: str
    allowed_model_prefixes: tuple[str, ...]
    external_tools_enabled: bool
    cache_enabled: bool

    @staticmethod
    def build(confidential: bool, vk_standard: str, vk_confidential: str) -> "PrivacyMode":
        if confidential:
            # Caching is disabled rather than merely restricted: a semantic
            # cache lookup embeds the request through the configured
            # embedding provider, which is a cloud one. Direct-only caching
            # would avoid that, but disabling it here keeps the privacy
            # controls in one place instead of depending on the gateway
            # honouring a per-request header.
            return PrivacyMode(
                confidential=True,
                virtual_key=vk_confidential,
                allowed_model_prefixes=(LOCAL_PREFIX,),
                external_tools_enabled=False,
                cache_enabled=False,
            )
        return PrivacyMode(
            confidential=False,
            virtual_key=vk_standard,
            allowed_model_prefixes=(),   # empty tuple means "no restriction"
            external_tools_enabled=True,
            cache_enabled=True,
        )

    def _permitted(self, model: str) -> bool:
        if not self.allowed_model_prefixes:
            return True
        return any(model.startswith(p) for p in self.allowed_model_prefixes)

    def assert_egress(self, model: str, fallbacks: list[str], virtual_key: str) -> None:
        """Last line of defence, called immediately before every HTTP call.

        Checks the virtual key, the primary model, and every fallback. The
        fallback check is the one most easily forgotten: a cloud model in
        the chain is an exfiltration path that activates exactly when the
        local model is unavailable.
        """
        if not self.confidential:
            return
        if virtual_key != self.virtual_key:
            raise PrivacyViolation(
                f"confidential mode: wrong virtual key at egress "
                f"(got {virtual_key!r}, expected the confidential key)"
            )
        if not self._permitted(model):
            raise PrivacyViolation(
                f"confidential mode: model {model!r} is not local "
                f"(allowed prefixes: {self.allowed_model_prefixes})"
            )
        for fb in fallbacks or []:
            if not self._permitted(fb):
                raise PrivacyViolation(
                    f"confidential mode: fallback {fb!r} is not local. "
                    "A cloud fallback fires precisely when the local model is down."
                )

    def describe(self) -> str:
        if not self.confidential:
            return "standard mode"
        return ("CONFIDENTIAL mode: local models only, web search disabled, "
                "no fallback chain, caching off")
