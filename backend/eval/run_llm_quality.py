import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from backend.agent.state import AgentState, RunConfig  # noqa: E402
from backend.llm.prompts import build_deploy_prompt  # noqa: E402
from backend.llm.vision import call_text_llm  # noqa: E402
from backend.security.error_sanitizer import sanitize_error  # noqa: E402

from .scenarios import SCENARIOS  # noqa: E402

PROVIDERS = ["gemini", "openai", "anthropic", "ollama", "cerebras", "glm"]

# env var each provider needs; None means the provider has a default config and is
# always attemptable (ollama defaults OLLAMA_BASE_URL to localhost:11434).
_PROVIDER_ENV_VARS = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "ollama": None,
    "cerebras": "CEREBRAS_API_KEY",
    "glm": "GLM_API_KEY",
}

_REQUIRED_KEYS = [
    "thought",
    "observation",
    "action",
    "element_id",
    "text_input",
    "direction",
    "grid_cell",
    "secret_id",
]

REPORT_PATH = Path(__file__).resolve().parent / "reports" / "latest.json"


def is_provider_configured(provider: str) -> bool:
    env_var = _PROVIDER_ENV_VARS[provider]
    return env_var is None or bool(os.environ.get(env_var))


def _make_state(scenario: dict) -> AgentState:
    config = RunConfig(
        app_name=scenario["app_name"],
        task=scenario["task"],
        mode="deploy",
        reasoning_mode="fast",
        provider="gemini",
    )
    return AgentState(session_id="eval", config=config)


def _score(decision: dict, scenario: dict) -> dict:
    schema_valid = all(k in decision for k in _REQUIRED_KEYS) and isinstance(
        decision.get("action"), str
    )
    action_valid = decision.get("action") in scenario["valid_actions"]
    grounded = decision.get("element_id") in scenario["valid_element_ids"]
    return {
        "schema_valid": bool(schema_valid),
        "action_valid": bool(action_valid),
        "grounded": bool(grounded),
        "overall": bool(schema_valid and action_valid and grounded),
    }


async def _run_case(provider: str, scenario: dict) -> dict:
    state = _make_state(scenario)
    prompt = build_deploy_prompt(state, scenario["elements"], docs_context="")
    result = {
        "provider": provider,
        "scenario": scenario["name"],
        "schema_valid": False,
        "action_valid": False,
        "grounded": False,
        "overall": False,
        "error": None,
        "action": None,
        "element_id": None,
    }
    try:
        decision = await call_text_llm(provider, prompt)
        result.update(_score(decision, scenario))
        result["action"] = decision.get("action")
        result["element_id"] = decision.get("element_id")
    except Exception as exc:
        result["error"] = sanitize_error(exc)
    return result


def _counts(results: list[dict], field: str) -> tuple[int, int]:
    total = len(results)
    passed = sum(1 for r in results if r[field])
    return passed, total


def _fmt_ratio(passed: int, total: int) -> str:
    pct = (passed / total * 100) if total else 0.0
    return f"{passed}/{total} ({pct:.0f}%)"


def _print_summary(results: list[dict], skipped: list[str]) -> None:
    print("\nPer-provider decision quality (real LLM calls, text/fast mode):")
    print(f"{'provider':<12} {'schema_valid':<17} {'action_valid':<17} {'grounded':<17} overall")
    for provider in PROVIDERS:
        if provider in skipped:
            continue
        cases = [r for r in results if r["provider"] == provider]
        row = [provider.ljust(12)]
        for field in ("schema_valid", "action_valid", "grounded", "overall"):
            passed, total = _counts(cases, field)
            ratio = _fmt_ratio(passed, total)
            row.append(ratio.ljust(17) if field != "overall" else ratio)
        print(" ".join(row))
    if skipped:
        print(f"\nSkipped (not configured): {', '.join(skipped)}")


def _build_report(results: list[dict], skipped: list[str]) -> dict:
    providers = {}
    for provider in PROVIDERS:
        if provider in skipped:
            continue
        cases = [r for r in results if r["provider"] == provider]
        fields = ("schema_valid", "action_valid", "grounded", "overall")
        summary = {
            field: {"passed": passed, "total": total}
            for field, (passed, total) in ((f, _counts(cases, f)) for f in fields)
        }
        providers[provider] = {"summary": summary, "cases": cases}
    return {"providers": providers, "skipped": skipped}


async def _run_all() -> tuple[list[dict], list[str]]:
    skipped = [p for p in PROVIDERS if not is_provider_configured(p)]
    results = []
    for provider in PROVIDERS:
        if provider in skipped:
            print(f"Skip {provider}: not configured")
            continue
        for scenario in SCENARIOS:
            print(f"Calling {provider} · {scenario['name']} ...", flush=True)
            results.append(await _run_case(provider, scenario))
    return results, skipped


def main() -> None:
    results, skipped = asyncio.run(_run_all())
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(_build_report(results, skipped), indent=2), encoding="utf-8")
    _print_summary(results, skipped)
    print(f"\nReport written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
