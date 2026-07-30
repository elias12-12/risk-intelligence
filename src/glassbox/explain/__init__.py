"""§13's explanation surfaces — deterministic, and reading one alert only.

`copilot` answers the three chips; `case_report` renders the filing draft. Both
build on `evidence.load`, which is the single place that decides what "the alert
in view" means and refuses to read anything else.
"""
from .case_report import build_report
from .copilot import answer_chips
from .evidence import ALLOWED_RELATIONS, AlertEvidence, load

__all__ = ["ALLOWED_RELATIONS", "AlertEvidence", "answer_chips", "build_report",
           "load"]
