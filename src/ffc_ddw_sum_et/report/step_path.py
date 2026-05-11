"""Plotly step-path construction.

A "step path" represents a piecewise-constant series as a polyline that
holds each y value horizontally until the next x, then drops vertically
to the new value. Plotly's ``mode: lines`` plus this expansion renders
the staircase shape used by the mean-RPDf-over-time charts.
"""


def build_step_path(
    x_values: list[float], y_values: list[float]
) -> tuple[list[float], list[float]]:
    """Expand ``(x_values, y_values)`` into staircase coordinates suitable
    for a ``mode: lines`` Plotly trace.

    At every transition where ``y_values[i] < y_values[i - 1]`` the output
    contains both the held-previous-y and the new-y points at the same x,
    which makes Plotly draw the vertical drop. Pure helper — no domain
    knowledge.
    """
    step_x: list[float] = []
    step_y: list[float] = []
    for idx, (x, y) in enumerate(zip(x_values, y_values)):
        if idx == 0:
            step_x.append(x)
            step_y.append(y)
            continue
        prev_y = y_values[idx - 1]
        step_x.append(x)
        step_y.append(prev_y)
        if y < prev_y:
            step_x.append(x)
            step_y.append(y)
    return step_x, step_y
