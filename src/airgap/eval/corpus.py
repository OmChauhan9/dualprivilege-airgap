"""Attack and benign corpus. Deterministic, so every run scores the same cases.

Rung 1 (~30 cases): each scenario crossed with each injection channel. Rung 2 grows this with
in-policy attacks and held-out variants; nothing here is tuned against the defense.
"""

from __future__ import annotations

from dataclasses import dataclass

from airgap.world.scenarios import CATALOG, SCENARIOS


@dataclass(frozen=True)
class Case:
    id: str
    scenario: str
    channel: str | None  # None means a benign page
    category: str

    @property
    def is_attack(self) -> bool:
        return self.channel is not None


_CATEGORY = {
    "css_display_none": "hidden_text",
    "white_on_white": "hidden_text",
    "off_screen": "hidden_text",
    "tiny_font": "hidden_text",
    "html_comment": "hidden_text",
    "zero_width_visible": "hidden_text",
    "json_ld": "poisoned_metadata",
    "og_meta": "poisoned_metadata",
    "alt_text": "poisoned_metadata",
    "visible_text": "visible_text",
}


def attack_cases() -> list[Case]:
    cases = []
    for sc in SCENARIOS.values():
        for channel in sc.channels:
            category = "data_steering" if sc.id == "steering" else _CATEGORY[channel]
            cases.append(Case(f"{sc.id}:{channel}", sc.id, channel, category))
    return cases


def benign_cases() -> list[Case]:
    """One benign task per catalog scenario: the defaults plus their parametrized variants."""
    return [Case(f"{sc.id}:benign", sc.id, None, "benign") for sc in CATALOG.values()]
