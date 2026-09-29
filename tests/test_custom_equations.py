"""Behavioural coverage for hand-entered kinetics and joint search."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from custom_equations import EquationError, compile_modules, module_ranges
from paper_app import PaperApp
from paper_catalog import MODELS, get_model
from paper_engine import save_result, simulate
from paper_search import candidate_parameters, group_total, run_search


def channel(kind="instant"):
    return {"name": "ChannelX", "current": "gX * m^3 * (V - EX)",
            "na_rate": "0", "ca_rate": "0",
            "variables": [{"name": "m", "kind": kind,
                           "equation": ("sigmoid((V - halfX + x) / slopeX)" if kind == "instant"
                                        else "(sigmoid((V - halfX + x) / slopeX) - m) / tauX"),
                           "initial": 0.2}],
            "parameters": [
                {"name": "gX", "value": .01, "low": .005, "high": .02,
                 "distribution": "log", "unit": "mS/cm²"},
                {"name": "EX", "value": 55, "low": 40, "high": 70,
                 "distribution": "uniform", "unit": "mV"},
                {"name": "halfX", "value": -35, "low": -50, "high": -20,
                 "distribution": "uniform", "unit": "mV"},
                {"name": "slopeX", "value": 7, "low": 3, "high": 12,
                 "distribution": "uniform", "unit": "mV"},
                {"name": "tauX", "value": 10, "low": 2, "high": 30,
                 "distribution": "log", "unit": "ms"},
            ]}


class CustomEquationTests(unittest.TestCase):
    def test_ode_relaxation_uses_target_and_time_constant(self):
        spec = get_model("Sato 2025 NAN")
        module = channel()
        module["current"] = "gX * m^3 * h * (V - EX)"
        module["variables"].append({
            "name": "h", "kind": "ode", "ode_form": "relaxation",
            "equation": "sigmoid((V + xx) / 40)", "tau": "tau_h", "initial": 0.2})
        module["parameters"].extend([
            {"name": "xx", "value": 0, "low": -20, "high": 20,
             "distribution": "uniform", "unit": "mV"},
            {"name": "tau_h", "value": 20, "low": 5, "high": 100,
             "distribution": "log", "unit": "ms"},
        ])
        definitions = [module]
        compiled = compile_modules(definitions, spec["parameters"], spec["fixed"], ("Na_mM",))
        context = {**spec["parameters"], **spec["fixed"],
                   **{p["name"]: p["value"] for p in module["parameters"]},
                   "V": -40, "Na": 1, "t": 0}
        derivative = compiled[0].evaluate([0.2], context)[3][0]
        target = 1/(1+np.exp(1))
        self.assertAlmostEqual(derivative, (target - .2) / 20)
        context["tau_h"] = 0
        with self.assertRaisesRegex(EquationError, "time constant must be positive"):
            compiled[0].evaluate([0.2], context)
        result = simulate("Sato 2025 NAN", record_ms=10, custom_modules=definitions)
        h = result["states"][:, result["state_names"].index("ChannelX.h")]
        self.assertTrue(np.isfinite(h).all())
        self.assertGreaterEqual(h.min(), 0)
        self.assertLessEqual(h.max(), 1)
        legacy = {**module, "variables": [{"name": "h", "kind": "ode",
                                            "equation": "sigmoid((V + xx) / 40)",
                                            "initial": .2}], "current": "gX * h * (V - EX)"}
        self.assertAlmostEqual(compile_modules([legacy], spec["parameters"], spec["fixed"],
                                               ("Na_mM",))[0].evaluate([.2], context | {"tau_h": 20})
                               [3][0], target)

    def test_instant_and_ode_gates_reference_existing_shift(self):
        for mode in ("instant", "ode"):
            module = channel(mode)
            result = simulate("Sato 2025 NAN", record_ms=10, custom_modules=[module])
            self.assertEqual(result["time_ms"][-1], 20000)
            self.assertEqual(result["analysis_from_ms"], 10000)
            self.assertEqual("ChannelX.m" in result["state_names"], mode == "ode")
            self.assertTrue(np.isfinite(result["states"]).all())
            with tempfile.TemporaryDirectory() as root:
                save_result(result, root)
                self.assertTrue((Path(root)/"trace.pdf").read_bytes().startswith(b"%PDF"))
                self.assertFalse((Path(root)/"trace.png").exists())
                self.assertEqual(json.loads((Path(root)/"config_metrics.json").read_text())
                                 ["custom_modules"][0]["name"], "ChannelX")

    def test_ion_coupling_and_math_validation(self):
        spec = get_model("Sato 2025 NAN")
        item = channel()
        item["na_rate"] = "-0.00001 * I"
        compiled = compile_modules([item], spec["parameters"], spec["fixed"], ("Na_mM",))
        current, na_rate, ca_rate, _ = compiled[0].evaluate(
            [], {**spec["parameters"], **spec["fixed"], **{p["name"]: p["value"] for p in item["parameters"]},
                 "V": -40, "Na": 1, "t": 0})
        self.assertAlmostEqual(na_rate, -0.00001*current)
        self.assertEqual(ca_rate, 0)
        for expression in ("__import__('os').system('true')", "V.__class__", "h^4^m(",
                           "unknown + V"):
            invalid = channel()
            invalid["current"] = expression
            with self.assertRaises(EquationError):
                compile_modules([invalid], spec["parameters"], spec["fixed"], ("Na_mM",))
        cycle = channel()
        cycle["variables"] = [{"name": "m", "kind": "instant", "equation": "n", "initial": 0},
                              {"name": "n", "kind": "instant", "equation": "m", "initial": 0}]
        with self.assertRaisesRegex(EquationError, "dependency cycle"):
            compile_modules([cycle], spec["parameters"], spec["fixed"], ("Na_mM",))

    def test_custom_joint_random_and_sweep(self):
        model = "Sato 2025 NAN"
        module = channel()
        ranges = {**get_model(model)["ranges"], **module_ranges([module])}
        group = {"name": "joint", "kind": "random", "basis": "edited",
                 "samples": 2, "parameters": ["gX", "tauX"],
                 "points": {"gX": 2, "tauX": 2}}
        self.assertEqual(group_total(group), 2)
        values = [candidate_parameters(model, ranges, group, i, 93, 0,
                                       custom_modules=[module]) for i in range(2)]
        self.assertEqual(len({row["gX"] for row in values}), 2)
        self.assertEqual(len({row["tauX"] for row in values}), 2)
        grid = {**group, "kind": "sweep", "samples": 4}
        self.assertEqual(group_total(grid), 4)
        with tempfile.TemporaryDirectory() as root:
            rows = run_search(model, ranges, [group], root, workers=1, master_seed=93,
                              custom_modules=[module], retain=())
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["label"] != "ERROR" for row in rows), rows)
            self.assertEqual({"gX", "tauX"} <= set(rows[0]), True)
            self.assertEqual(json.loads((Path(root)/"search_config.json").read_text())
                             ["custom_modules"][0]["name"], "ChannelX")

    def test_every_model_uses_twenty_second_run_and_ten_second_analysis(self):
        for model in MODELS.values():
            self.assertEqual((model["duration_ms"], model["analysis_from_ms"]),
                             (20000, 10000))

    def test_one_control_changes_all_levels_and_queued_groups(self):
        class Holder:
            def __init__(self, value=None):
                self.value = value
            def get(self):
                return self.value
            def set(self, value):
                self.value = value

        app = object.__new__(PaperApp)
        app.all_points_var = Holder("7")
        app.status_var = Holder()
        app.points = {"gX": 100, "tauX": 100, "gK": 100}
        app.groups = [{"name": "joint", "kind": "random", "samples": 100,
                       "parameters": ["gX", "tauX"], "points": {"gX": 100, "tauX": 100}},
                      {"name": "grid", "kind": "sweep", "samples": 10000,
                       "parameters": ["gX", "gK"], "points": {"gX": 100, "gK": 100}}]
        app.refresh_params = lambda: None
        app.refresh_groups = lambda: None
        app.apply_all_points()
        self.assertEqual(app.points, dict.fromkeys(app.points, 7))
        self.assertEqual([group_total(group) for group in app.groups], [7, 49])


if __name__ == "__main__":
    unittest.main()
