"""Serializable quality evidence, independent of execution status.

Conservative aggregation: FAIL > WARN > UNKNOWN > PASS. Empty evidence is
UNKNOWN; an unknown check prevents a collection of passes becoming overall PASS.
No scientific acceptance thresholds are defined here.
"""
from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
from pathlib import Path

from .io import read_json, write_json


class QualityStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class QualityCheck:
    name: str
    status: QualityStatus
    message: str
    measured_value: object = None
    units: str | None = None
    thresholds: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "status", QualityStatus(self.status))
        if not self.name or not self.message:
            raise ValueError("Quality checks require a name and message")


@dataclass(frozen=True)
class RunQuality:
    checks: tuple[QualityCheck, ...] = ()
    evaluator: str = "unspecified"
    evaluator_version: str = "unspecified"

    @property
    def status(self):
        statuses = {check.status for check in self.checks}
        for status in (QualityStatus.FAIL, QualityStatus.WARN, QualityStatus.UNKNOWN):
            if status in statuses:
                return status
        return QualityStatus.PASS if statuses else QualityStatus.UNKNOWN

    def to_dict(self):
        return {"schema_version": 1, "status": self.status.value,
                "checks": [asdict(check) for check in self.checks],
                "evaluator": self.evaluator, "evaluator_version": self.evaluator_version}


def evaluate_run(directory):
    """Offline evaluator dispatcher. Mock events cannot establish optical quality.

Future evaluators must consume retained measurements and recipe thresholds,
reference their evidence, and record their own version. No console state is used.
"""
    directory = Path(directory)
    from .config import load_recipe
    recipe = load_recipe(directory / "recipe.json")
    if recipe.experiment != "mock_lifecycle":
        from .quality_eom import evaluate_eom
        return evaluate_eom(directory, recipe)
    evidence = {"evaluator_source": {"sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}}
    for name in ("recipe.json", "mock_events.jsonl"):
        path = directory / name
        evidence[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()} if path.exists() else {"missing": True}
    return RunQuality((QualityCheck(
        "scientific_quality", QualityStatus.UNKNOWN,
        "Lifecycle-only mock: no physical measurements or scientific checks are available.",
        evidence=evidence),), "mock_lifecycle", "1")


def reevaluate(directory):
    """Replace derived quality/report only; recipe, events and run.json stay intact."""
    directory = Path(directory)
    quality = evaluate_run(directory)
    write_json(directory / "quality.json", quality.to_dict())
    from .reports import create_report
    create_report(directory)
    return quality
