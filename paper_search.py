"""Reproducible parameter-combination searches with CPU process parallelism."""
from __future__ import annotations

import csv
import json
import math
import multiprocessing as mp
import os
import secrets
import time
from functools import lru_cache
from pathlib import Path

import numpy as np

from paper_catalog import (effective_model, validate_assignment, validate_ranges,
                           validate_disabled_channels)
from paper_engine import classify, save_result, simulate
from custom_equations import compile_modules, module_parameters, module_ranges

MAX_CONDITIONS = 2_000_000


def cpu_count():
    # Query the actual computer running the app, rather than a build machine.
    return max(1, os.cpu_count() or 1)


def default_workers():
    return min(4, max(1, cpu_count()-1))


def point_count(group, key):
    return int(group.get("points", {}).get(key, group["samples"]))


def group_total(group):
    if group["kind"] == "random":
        counts = {point_count(group, key) for key in group["parameters"]}
        if len(counts) != 1 or int(group["samples"]) not in counts:
            raise ValueError("Random requires the same draw count for every selected parameter")
        return counts.pop()
    return math.prod(point_count(group, key) for key in group["parameters"])


def choice_indices(group, index, master_seed, group_index):
    keys = group["parameters"]
    if group["kind"] == "sweep":
        if not 0 <= index < group_total(group):
            raise IndexError("Sweep condition index is outside the Cartesian product")
        remaining = index
        choices = {}
        for key in reversed(keys):
            remaining, choices[key] = divmod(remaining, point_count(group, key))
        return {key: choices[key] for key in keys}
    if not 0 <= index < group_total(group):
        raise IndexError("Random condition index is outside the requested count")
    choices = {}
    for axis, key in enumerate(keys):
        count = point_count(group, key)
        rng = np.random.default_rng(np.random.SeedSequence(
            [master_seed, group_index, axis, 0]))
        # Pair each independently drawn value once without a Cartesian product.
        choices[key] = int(rng.permutation(count)[index])
    return choices


@lru_cache(maxsize=1024)
def _grid(low, high, distribution, count, basis, baseline, bifurcation, shift):
    if basis == "paper":
        if shift:
            return baseline + np.linspace(-45.0, 45.0, count)
        if baseline <= 0:
            raise ValueError("A zero baseline needs an edited-range sweep")
        return baseline*np.geomspace(*bifurcation, count)
    if distribution == "neglog":
        return -np.geomspace(-low, -high, count)
    if distribution == "log":
        return np.geomspace(low, high, count)
    return np.linspace(low, high, count)


@lru_cache(maxsize=1024)
def _random_draws(low, high, distribution, count, master_seed, group_index, axis):
    rng = np.random.default_rng(np.random.SeedSequence(
        [master_seed, group_index, axis, 1]))
    if distribution == "log":
        return 10.0 ** rng.uniform(np.log10(low), np.log10(high), count)
    if distribution == "neglog":
        return -(10.0 ** rng.uniform(np.log10(-high), np.log10(-low), count))
    return rng.uniform(low, high, count)


def candidate_parameters(model_name, ranges, group, index, master_seed, group_index,
                         baseline_parameters=None, return_indices=False,
                         composition=None, custom_modules=None):
    spec = effective_model(model_name, composition)
    base = {**spec["parameters"], **module_parameters(custom_modules)}
    if baseline_parameters is not None:
        if set(baseline_parameters) != set(base):
            raise ValueError("Baseline parameter names must match the selected model")
        base.update(baseline_parameters)
    keys = group["parameters"]
    domains = {**ranges, **group.get("ranges", {})}
    choices = choice_indices(group, index, master_seed, group_index)
    for axis, key in enumerate(keys):
        lo, hi, distribution, _ = domains[key]
        if group["kind"] == "random":
            values = _random_draws(lo, hi, distribution, point_count(group, key),
                                   master_seed, group_index, axis)
        else:
            if group.get("basis", "edited") == "paper" and key not in spec["parameters"]:
                raise ValueError("Custom parameters use edited sweep bounds")
            values = _grid(lo, hi, distribution, point_count(group, key),
                           group.get("basis", "edited"), base[key],
                           spec["bifurcation"], key in ("x", "y"))
        base[key] = float(values[choices[key]])
    return (base, {key: choices[key]+1 for key in keys}) if return_indices else base


def _evaluate(task):
    (model_name, cell_role, parameters, fixed_values, group_name, index,
     candidate_seed, chosen, output, retain, disabled, composition, custom_modules) = task
    row = {"model": model_name, "cell_role": cell_role,
           "configuration": ("extended" if custom_modules else
                             "composed" if composition is not None else "published"),
           "group": group_name, "index": index,
           "sampling_seed": candidate_seed,
           "choice_indices": ", ".join(f"{key}{number}" for key, number in chosen.items()),
           **parameters}
    try:
        result = simulate(model_name, parameters, fixed_overrides=fixed_values,
                          disabled_channels=disabled, composition=composition,
                          custom_modules=custom_modules)
        result["cell_role"] = cell_role
        metric = classify(result)
        row.update(metric)
        if metric["label"] in retain:
            path = Path(output)/group_name/f"candidate_{index:06d}"
            save_result(result, path)
            row["trace_path"] = str(path.relative_to(output))
        else:
            row["trace_path"] = ""
    except Exception as exc:
        row.update({"label": "ERROR", "error": f"{type(exc).__name__}: {exc}",
                    "trace_path": ""})
    return row


def _valid_group_name(name):
    name = str(name).strip()
    if not name or any(ch in name for ch in "/\\:") or name in {".", ".."}:
        raise ValueError("Combination names cannot be empty or contain path separators")
    return name


def run_search(model_name, ranges, groups, output, workers=None, master_seed=None,
               retain=("SWO_candidate",), progress=None, stop=None,
               baseline_parameters=None, fixed_overrides=None, cell_role="E",
               disabled_channels=(), composition=None, custom_modules=None):
    spec = effective_model(model_name, composition)
    extras = module_parameters(custom_modules)
    extra_ranges = module_ranges(custom_modules)
    # Compile before writing output so malformed expressions fail immediately.
    state_names = ("Na_mM", "Ca_uM") if spec["family"] in ("FNAN", "COMPOSED") else (
        ("Na_mM",) if spec["family"] == "NAN" else ("Ca_uM",))
    compile_modules(custom_modules, spec["parameters"], spec["fixed"], state_names)
    search_spec = {**spec, "ranges": {**spec["ranges"], **extra_ranges}}
    validate_assignment(cell_role, model_name)
    disabled = validate_disabled_channels(model_name, disabled_channels, composition)
    validate_ranges(search_spec, ranges, [group["parameters"] for group in groups])
    if len({_valid_group_name(group["name"]) for group in groups}) != len(groups):
        raise ValueError("Combination names must be unique")
    for group in groups:
        if set(group["parameters"]) & set(disabled):
            raise ValueError("An off channel cannot be a search axis; enable it first")
        if not set(group.get("ranges", {})) <= set(group["parameters"]):
            raise ValueError("Combination bounds must match its parameter selection")
        validate_ranges(search_spec, {**ranges, **group.get("ranges", {})}, [group["parameters"]])
        if group["kind"] not in ("random", "sweep"):
            raise ValueError("Select random or sweep for each combination")
        if not all(1 <= point_count(group, key) <= 1000 for key in group["parameters"]):
            raise ValueError("Each parameter needs between 1 and 1000 draws or sweep levels")
        if not 1 <= group_total(group) <= MAX_CONDITIONS:
            raise ValueError(f"Each combination must contain 1–{MAX_CONDITIONS:,} conditions")
        if group["kind"] == "sweep" and group.get("basis", "paper") not in ("paper", "edited"):
            raise ValueError("Sweep basis must be paper or edited")
        if (composition is not None or set(group["parameters"]) & set(extras)) and (
                group["kind"] == "sweep" and group.get("basis", "paper") != "edited"):
            raise ValueError("Custom parameters and assemblies use edited sweep bounds")
    if sum(group_total(group) for group in groups) > MAX_CONDITIONS:
        raise ValueError(f"A search can contain at most {MAX_CONDITIONS:,} conditions")
    workers = default_workers() if workers is None else int(workers)
    if not 1 <= workers <= cpu_count():
        raise ValueError(f"Workers must be between 1 and {cpu_count()}")
    master_seed = secrets.randbits(63) if master_seed is None else int(master_seed)
    if master_seed < 0 or master_seed >= 2**63:
        raise ValueError("Invalid sampling seed")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    selected_baseline = dict({**spec["parameters"], **extras} if baseline_parameters is None
                             else baseline_parameters)
    baseline = dict(selected_baseline)
    for key in disabled:
        baseline[key] = 0.0
    fixed = dict(spec["fixed"] if fixed_overrides is None else fixed_overrides)
    if set(baseline) != set(spec["parameters"]) | set(extras):
        raise ValueError("Baseline parameter names must match the selected model")
    if set(fixed) != set(spec["fixed"]):
        raise ValueError("Fixed parameter names must match the PDF model")
    if any(not math.isfinite(value) or (key in spec["parameters"] and key not in ("x", "y") and value < 0)
           for key, value in baseline.items()):
        raise ValueError("Invalid baseline parameter value")
    for group in groups:
        if group["kind"] == "sweep" and group.get("basis", "paper") == "paper":
            for key in group["parameters"]:
                if key in extras:
                    raise ValueError("Custom parameters use edited sweep bounds: " + key)
                if key not in ("x", "y") and baseline[key] == 0:
                    raise ValueError("A zero baseline needs an edited-range sweep: " + key)
    config = {
        "model": model_name, "source_equations": spec["equations"],
        "configuration": ("extended" if custom_modules else
                          "composed" if composition is not None else "published"),
        "composition": spec.get("composition"),
        "custom_modules": custom_modules or [],
        "cell_role": cell_role,
        "cell_role_note": "The E/I role is a research assignment, not a cell-type-specific fit",
        "source_baseline": spec["baseline"], "source_search": spec["search"],
        "fixed_values": fixed, "published_fixed": spec["fixed"],
        "published_baseline": spec["parameters"],
        "selected_baseline_before_channel_switches": selected_baseline,
        "baseline_parameters": baseline,
        "disabled_channels": list(disabled),
        "default_ranges": search_spec["ranges"], "ranges": ranges,
        "combinations": groups, "master_seed": master_seed,
        "cpu_count_reported": cpu_count(), "workers_used": workers,
        "retained_labels": list(retain),
        "note": "Deterministic ODE; seeds generate parameter draws, not membrane noise. "
                "SWO_candidate requires visual review. Per-combination bounds are recorded.",
    }
    (output/"search_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2)+"\n",
                                              encoding="utf-8")
    total = sum(group_total(group) for group in groups)
    def task_stream():
        for gi, group in enumerate(groups):
            for i in range(group_total(group)):
                params, chosen = candidate_parameters(model_name, ranges, group, i,
                                                       master_seed, gi, baseline, True,
                                                       composition=composition,
                                                       custom_modules=custom_modules)
                # The draw is reconstructed by (master, group, index).
                yield (model_name, cell_role, params, fixed, group["name"], i,
                       f"{master_seed}:{gi}:{i}", chosen, str(output), tuple(retain),
                       disabled, spec.get("composition"), custom_modules)

    tasks = iter(task_stream())
    rows = []
    columns = ["model", "cell_role", "configuration", "group", "index", "sampling_seed", "choice_indices",
               *spec["parameters"], *extras,
               "label", "peak_hz", "spikes_per_s", "min_mV", "max_mV",
               "manual_review_required", "trace_path", "error"]
    context = mp.get_context("spawn")
    pool = context.Pool(processes=workers)
    stopped = False
    finished_normally = False
    completed = 0
    counts = {}
    collect_rows = total <= 10000
    try:
        pending = []
        with (output/"summary.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            while completed < total:
                if stop is not None and stop.is_set():
                    stopped = True
                    break
                while len(pending) < max(2*workers, 8):
                    try:
                        pending.append(pool.apply_async(_evaluate, (next(tasks),)))
                    except StopIteration:
                        break
                ready = next((i for i, job in enumerate(pending) if job.ready()), None)
                if ready is None:
                    time.sleep(.1)
                    continue
                row = pending.pop(ready).get()
                completed += 1
                counts[row["label"]] = counts.get(row["label"], 0)+1
                if collect_rows:
                    rows.append(row)
                writer.writerow(row)
                f.flush()
                if progress:
                    progress(completed, total, row)
        finished_normally = not stopped
    finally:
        if not finished_normally:
            pool.terminate()
        else:
            pool.close()
        pool.join()
    (output/"status.json").write_text(json.dumps(
        {"completed": completed, "total": total, "stopped": stopped,
         "labels": counts}, indent=2)+"\n")
    return rows
