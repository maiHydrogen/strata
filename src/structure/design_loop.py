"""Structural design loop: write .std -> run STAAD -> parse -> resize, until all checks pass."""

from dataclasses import dataclass, field

from src.structure.sizing import resize
from src.structure.staad_results import StaadResults, parse_anl
from src.structure.std_writer import Frame, write_std


@dataclass
class DesignRound:
    number: int
    sections: dict  # group -> "WxD" in mm
    passed: bool
    changes: list
    warnings: list


@dataclass
class DesignOutcome:
    frame: Frame
    results: StaadResults
    passed: bool
    reason: str = ""
    rounds: list = field(default_factory=list)


def _sizes(frame):
    return {g: f"{s.width * 1000:.0f}x{s.depth * 1000:.0f}" for g, s in sorted(frame.sections.items())}


def design(frame: Frame, runner, name="strata_frame", max_rounds=6) -> DesignOutcome:
    """`runner(std_text, name) -> anl_text` runs STAAD (StaadRunner, ManualRunner or a stub)."""
    rounds = []
    deepened = frozenset()
    for n in range(1, max_rounds + 1):
        results = parse_anl(runner(write_std(frame, title=f"{name} round {n}"), f"{name}_r{n}"))
        if results.all_passed:
            rounds.append(DesignRound(n, _sizes(frame), True, [], results.warnings))
            return DesignOutcome(frame, results, True, rounds=rounds)
        new_frame, changes, feasible, deepened = resize(frame, results, deepened)
        rounds.append(DesignRound(n, _sizes(frame), False, changes, results.warnings))
        if not feasible:
            return DesignOutcome(frame, results, False, reason=changes[-1], rounds=rounds)
        if not changes:
            return DesignOutcome(frame, results, False,
                                 reason="checks failed but no resize rule applies", rounds=rounds)
        frame = new_frame
    return DesignOutcome(frame, results, False,
                         reason=f"not converged after {max_rounds} rounds", rounds=rounds)
