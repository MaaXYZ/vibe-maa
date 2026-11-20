"""Pinned draw.io deployment and independent canvas oracle."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from browser_support import BrowserTarget, serve_directory

from vibe_maa.targets.drawio.prepare import manifest, provision

INIT_SCRIPT = """
window.addEventListener('load', () => {
    const init = App.prototype.init;
    App.prototype.init = function() {
        window.__vibe_grader_editor = this;
        return init.apply(this, arguments);
    };
}, {once: true});
"""


@contextmanager
def launch(cache: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    metadata = manifest()
    with serve_directory(provision(cache)) as origin:
        yield origin + metadata["launch_path"], metadata


def ready(page: Any) -> None:
    page.wait_for_function(
        "window.__vibe_grader_editor && __vibe_grader_editor.getCurrentFile() "
        "&& __vibe_grader_editor.editor.graph.isEnabled()",
        timeout=10000,
    )


def snapshot(page: Any) -> list[dict[str, Any]]:
    return page.evaluate("""() => {
        const graph = window.__vibe_grader_editor.editor.graph;
        return Object.values(graph.model.cells).filter(cell => cell.vertex).map(cell => ({
            label: graph.convertValueToString(cell), style: cell.style || '',
            shape: graph.getCellStyle(cell).shape,
            rectangle_renderer: graph.view.getState(cell).shape instanceof mxRectangleShape,
            ellipse_renderer: graph.view.getState(cell).shape instanceof mxEllipse,
            width: cell.geometry.width, height: cell.geometry.height
        }));
    }""")


def completed(before: list[dict[str, Any]], document: list[dict[str, Any]]) -> bool:
    # The default rectangular palette shape uses mxLabel, a subclass of mxRectangleShape.
    return (
        not before
        and len(document) == 1
        and document[0]["label"] == "Vibe Maa"
        and document[0].get("rectangle_renderer") is True
        and document[0].get("ellipse_renderer") is False
        and document[0]["width"] > 0
        and document[0]["height"] > 0
    )


TARGET = BrowserTarget(
    objective="Create a rectangle in the blank diagrams.net canvas and label it exactly Vibe Maa.",
    oracle="fresh real application canvas DOM, independent from agent-supplied goals",
    launch=lambda cache, _: launch(cache),
    ready=ready,
    snapshot=snapshot,
    completed=completed,
    init_script=INIT_SCRIPT,
)
