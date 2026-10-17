"""Measure interpreter overhead without browser I/O.

Run from the repository root with:
    .venv/Scripts/python.exe benchmarks/benchmark_interpreter.py
"""

import argparse
import asyncio
import cProfile
import statistics
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import yaml

from web_page import WebPage
from web_page.protocols import Page
from web_page.utils import SelectorTemplates, fill, prepare_template


class NoIOPage:
    """Only the methods used by these workloads, with no browser latency."""

    def locator(self, selector: str) -> NoIOPage:
        return self

    async def fill(self, value: str, **kwargs: object) -> None:
        pass


class RelinkingWebPage(WebPage):
    """Diagnostic control: repeat dependency linking instead of reusing it."""

    def _link(self, action_name, stack=()):
        self._linked.clear()
        return super()._link(action_name, stack)


async def workloads(
    page_type: type[WebPage] = WebPage,
) -> dict[str, Callable[[], Awaitable[object]]]:
    page = page_type(cast(Page, NoIOPage()))
    flat = [{"fill": {"target": "item", "value": "prefix-{id}"}}] * 50
    actions = {
        "flat": flat,
        "repeat": [{"repeat": {"times": 50, "steps": flat[:1]}}],
        "child": flat[:1],
        "nested": [{"run": {"action": "child", "params": {"id": "{id}"}}}] * 20,
    }
    # Exercise the public load/compile path. All setup is outside timed batches.
    with tempfile.TemporaryDirectory(
        prefix=".benchmark-", dir=Path(__file__).resolve().parents[1]
    ) as temporary:
        manifest = Path(temporary) / "manifest.yaml"
        manifest.write_text(
            yaml.safe_dump(
                {"name": "s", "selectors": {"item": "[data-id='{id:string}']"}}
            ),
            encoding="utf-8",
        )
        await page.load_selectors(manifest)
        for name, raw in actions.items():
            manifest.write_text(
                yaml.safe_dump(
                    {
                        "name": name,
                        "selectors_name": "s",
                        "params": ["id"],
                        "steps": raw,
                    }
                ),
                encoding="utf-8",
            )
            await page.load_action(manifest)

    params = {"id": "sample"}
    selectors = SelectorTemplates(
        {f"item{i}": prepare_template("#{id}") for i in range(1000)}, params, {}
    )

    async def templates() -> None:
        for _ in range(50):
            fill("literal text", params)
            fill("[data-id='{id:string}']-{result.saved}", params, {"saved": "new"})

    async def membership() -> None:
        assert "item999" in selectors
        assert "absent" not in selectors

    return {
        "templates (100 fills)": templates,
        "selector membership (1000 keys)": membership,
        **{
            name: lambda name=name: page.run(name, params)
            for name in ("flat", "repeat", "nested")
        },
    }


async def main(runs: int, repeats: int, profile: bool, compare_linking: bool) -> None:
    cases = await workloads()
    controls = await workloads(RelinkingWebPage) if compare_linking else {}
    for name, workload in cases.items():
        operations = [workload, controls[name]] if controls else [workload]
        samples: dict[Callable[[], Awaitable[object]], list[float]] = {
            operation: [] for operation in operations
        }
        for operation in operations:
            for _ in range(10):
                await operation()
        for batch in range(repeats):
            # Alternate controls and production to reduce system-load/order bias.
            ordered = operations if batch % 2 == 0 else list(reversed(operations))
            for operation in ordered:
                started = time.perf_counter()
                for _ in range(runs):
                    await operation()
                samples[operation].append(
                    (time.perf_counter() - started) * 1_000_000 / runs
                )
        median = statistics.median(samples[workload])
        output = f"{name:34} {median:10.2f} us/run"
        if controls:
            control = statistics.median(samples[controls[name]])
            output += f"  relinking: {control:10.2f} us/run ({control / median:.2f}x)"
        print(output)
    if profile:
        profiler = cProfile.Profile()
        profiler.enable()
        for _ in range(runs):
            await cases["flat"]()
        profiler.disable()
        profiler.print_stats(sort="cumulative")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=500)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--compare-linking", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.runs, args.repeats, args.profile, args.compare_linking))
