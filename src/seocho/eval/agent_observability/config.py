from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Strategy = Literal[
    "vanilla",
    "global_budget",
    "planned_vanilla",
    "planned_global_budget",
    "plan_and_budget",
    "routed_plan_and_budget",
    "tool_agent",
]
Tier = Literal["T0_PARAMETRIC", "T1_RETRIEVAL", "T2_REFERENCE"]


class PublicTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    difficulty: float = Field(default=1, gt=0)


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    condition_id: str = Field(min_length=1)
    strategy: Strategy
    tier_policy: Literal["forced", "agent_decided"]
    tier: Tier | None = None
    router: Literal["static", "llm"] = "static"
    delegation: Literal["single", "parallel"] = "parallel"
    soft_budget: int = Field(default=512, gt=0)
    hard_output_limit: int = Field(default=1024, gt=0)
    execution_path: Literal["none", "direct", "explicit", "adaptive"] = "none"
    metacognition: bool = False
    max_tool_calls: int = Field(default=16, gt=0)
    max_calls: int = Field(default=24, gt=0)
    output_contract: Literal["api_schema", "prompt_json"] = "api_schema"
    evaluator_output_limit: int | None = Field(default=None, gt=0)
    coordination: Literal[
        "independent", "sequential_context", "peer_review", "adaptive_review"
    ] = "independent"
    worker_identity: Literal["per_task", "single"] = "per_task"
    judge_coordination: bool = True
    fixed_tasks: list[PublicTask] = Field(default_factory=list, max_length=8)
    prompt_order: Literal["question_first", "evidence_first"] = "question_first"
    returned_context_order: Literal["forward", "reverse"] = "forward"

    @model_validator(mode="after")
    def policy(self) -> Condition:
        if (self.tier_policy == "forced") != (self.tier is not None):
            raise ValueError(
                "Only forced policy declares tier; agent_decided must choose at runtime"
            )
        if self.execution_path != "none" and (
            self.strategy != "vanilla" or self.metacognition
        ):
            raise ValueError("Execution path requires vanilla single-agent strategy")
        if self.metacognition and self.strategy != "tool_agent":
            raise ValueError("Metacognition requires single tool_agent strategy")
        if self.strategy == "tool_agent" and self.output_contract != "api_schema":
            raise ValueError("tool_agent requires API schema output")
        if self.strategy == "tool_agent" and self.tier_policy != "forced":
            raise ValueError("tool_agent requires an explicit fixed information tier")
        if len({t.task_id for t in self.fixed_tasks}) != len(self.fixed_tasks):
            raise ValueError("Public task IDs must be unique")
        if (
            self.fixed_tasks or self.coordination != "independent"
        ) and self.strategy in ("vanilla", "global_budget", "tool_agent"):
            raise ValueError("Coordination and fixed tasks require a planned strategy")
        if self.coordination == "sequential_context" and self.delegation != "single":
            raise ValueError("Sequential context requires single delegation")
        if self.worker_identity == "single" and (
            not self.fixed_tasks or self.coordination != "sequential_context"
        ):
            raise ValueError("Single worker requires fixed sequential context tasks")
        return self


class Experiment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str = Field(min_length=1)
    recorder_url: str
    dataset: Path
    evaluation: Path
    seeds: list[int] = Field(min_length=1)
    samples: int = Field(default=1, gt=0)
    models: dict[str, str]
    conditions: list[Condition] = Field(min_length=1)
    template_version: Literal["v1"] = "v1"
    evaluator_version: Literal["v1", "v2"] = "v1"
    harness_backend: Literal["openai_agents", "pi"] = "openai_agents"
    pi_executable: str = "pi"
    pi_artifacts: Path = Path("outputs/pi-harness")

    @model_validator(mode="after")
    def unique(self) -> Experiment:
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("Seeds must be unique")
        if (
            any(c.metacognition or c.execution_path != "none" for c in self.conditions)
            and self.evaluator_version != "v2"
        ):
            raise ValueError("Metacognition requires evaluator v2")
        ids = [c.condition_id for c in self.conditions]
        if len(ids) != len(set(ids)):
            raise ValueError("Condition IDs must be unique")
        if (
            not {"planner", "solver", "router", "synthesis", "evaluator"}
            <= self.models.keys()
        ):
            raise ValueError("All model roles must be explicitly mapped")
        return self


def load_experiment(path: Path) -> Experiment:
    import yaml

    result = Experiment.model_validate(yaml.safe_load(path.read_text()))
    if not result.dataset.is_absolute():
        result.dataset = path.parent / result.dataset
    if not result.evaluation.is_absolute():
        result.evaluation = path.parent / result.evaluation
    return result
