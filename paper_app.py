"""GUI for the PDF equation sets and paper-style parameter searches."""
from __future__ import annotations

import copy
import csv
import json
import math
import multiprocessing
import queue
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import ImageTk, Image
import pypdfium2 as pdfium

from custom_equations import compile_modules, module_parameters, module_ranges
from equation_editor import ModuleEditor
from paper_catalog import (CELL_ROLE_MODELS, CHANNEL_LABELS,
                           available_channels, channel_sources, compose_model,
                           effective_model, get_model, validate_assignment)
from paper_engine import save_result, simulate
from paper_formulas import equations_for
from paper_search import cpu_count, default_workers, group_total, run_search


class ParameterDialog(tk.Toplevel):
    def __init__(self, parent, key, value, domain, points=None, allow_negative=False):
        super().__init__(parent)
        self.key = key
        self.title(key + " settings")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.result = None
        self.points = points
        self.allow_negative = allow_negative
        outer = ttk.Frame(self, padding=18)
        outer.grid()
        self.variables = {
            "value": tk.StringVar(value=str(value)),
            "low": tk.StringVar(value=str(domain[0])),
            "high": tk.StringVar(value=str(domain[1])),
            "distribution": tk.StringVar(value=domain[2]),
        }
        fields = [
            ("value", "Representative value"), ("low", "Lower bound"),
            ("high", "Upper bound"), ("distribution", "Sampling distribution")]
        if points is not None:
            self.variables["points"] = tk.StringVar(value=str(points))
            fields.append(("points", "Draws / sweep levels"))
        for row, (field, label) in enumerate(fields):
            ttk.Label(outer, text=label).grid(row=row, column=0, sticky="w", pady=6, padx=(0, 12))
            if field == "distribution":
                widget = ttk.Combobox(outer, textvariable=self.variables[field],
                                      state="readonly", values=("log", "uniform", "neglog"), width=22)
            else:
                widget = ttk.Entry(outer, textvariable=self.variables[field], width=25)
            widget.grid(row=row, column=1, sticky="w", pady=6)
        ttk.Label(outer, text="log / neglog space positive / negative values geometrically. Settings are saved.",
                  foreground="#526070").grid(row=len(fields), column=0, columnspan=2,
                                                sticky="w", pady=9)
        ttk.Button(outer, text="Save", command=self.finish).grid(row=len(fields)+1,
                                                                  column=1, sticky="e", pady=5)
        self.bind("<Escape>", lambda event: self.destroy())
        self.bind("<Return>", lambda event: self.finish())

    def finish(self):
        try:
            value = float(self.variables["value"].get())
            low = float(self.variables["low"].get())
            high = float(self.variables["high"].get())
            dist = self.variables["distribution"].get()
            points = int(self.variables["points"].get()) if self.points is not None else None
            if (not all(math.isfinite(number) for number in (value, low, high))
                    or not low < high or dist not in ("log", "uniform", "neglog")
                    or dist == "log" and low <= 0
                    or dist == "neglog" and high >= 0
                    or not self.allow_negative and self.key not in ("x", "y") and value < 0
                    or points is not None and points < 1):
                raise ValueError("Check the value, bounds, distribution and draw / level count")
            self.result = value, low, high, dist, points
            self.destroy()
        except ValueError as exc:
            messagebox.showerror("Parameter", str(exc), parent=self)


class PaperApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Channel Circuit Lab 1.0 Prime | Paper model search")
        self.geometry("1160x800")
        self.minsize(980, 690)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.worker = None
        self.role_var = tk.StringVar(value="E")
        self.active_role = "E"
        self.saved_models = {"E": "Sato 2025 NAN", "I": "Tatsuki 2016 AN"}
        self.model_var = tk.StringVar(value="Sato 2025 NAN")
        self.loaded_model = self.model_var.get()
        self.cpus = cpu_count()
        self.workers_var = tk.IntVar(value=default_workers())
        self.kind_var = tk.StringVar(value="random")
        self.sweep_basis_var = tk.StringVar(value="edited")
        self.output_var = tk.StringVar(value=str(Path.home()/"Documents"/"ChannelCircuitLab"/"PaperSearch"))
        self.status_var = tk.StringVar(value=f"Logical CPUs: {self.cpus} | Ready")
        self.progress_var = tk.StringVar(value="0 / 0")
        self.search_preview_var = tk.StringVar(value="")
        self.info_var = tk.StringVar()
        self.last_output = None
        self.preview = None
        self.groups = []
        self.points = {}
        self.all_points_var = tk.StringVar(value="100")
        self.composition = None
        self.custom_modules = []
        self.disabled_channels = set()
        self.channel_vars = {}
        self.selected_keys = set()
        self._build()
        self.load_model()
        self.after(100, self.poll)

    def _build(self):
        head = ttk.Frame(self, padding=12)
        head.pack(fill="x")
        ttk.Label(head, text="Paper model search", font=("Helvetica", 17, "bold")).pack(side="left", padx=(0, 18))
        ttk.Label(head, text="Cell").pack(side="left")
        role_box = ttk.Combobox(head, textvariable=self.role_var, state="readonly",
                                values=tuple(CELL_ROLE_MODELS), width=5)
        role_box.pack(side="left", padx=8)
        role_box.bind("<<ComboboxSelected>>", self.on_role_selected)
        ttk.Label(head, text="Model").pack(side="left")
        self.model_box = ttk.Combobox(head, textvariable=self.model_var,
                                      state="readonly", values=CELL_ROLE_MODELS["E"], width=27)
        self.model_box.pack(side="left", padx=8)
        self.model_box.bind("<<ComboboxSelected>>", self.on_model_selected)
        self.baseline_button = ttk.Button(head, text="Representative trace", command=self.run_baseline)
        self.baseline_button.pack(side="right")
        book = ttk.Notebook(self)
        book.pack(fill="both", expand=True, padx=12, pady=6)
        self.book = book
        self.params_tab = ttk.Frame(book, padding=12)
        self.channels_tab = ttk.Frame(book, padding=12)
        self.builder_tab = ttk.Frame(book, padding=12)
        self.custom_tab = ttk.Frame(book, padding=12)
        self.search_tab = ttk.Frame(book, padding=12)
        self.results_tab = ttk.Frame(book, padding=12)
        self.formulas_tab = ttk.Frame(book, padding=12)
        self.sources_tab = ttk.Frame(book, padding=12)
        for tab, title in ((self.params_tab, "Values and bounds"),
                           (self.channels_tab, "Channels"),
                           (self.builder_tab, "Build model"),
                           (self.custom_tab, "Custom equations"),
                           (self.search_tab, "Combinations and run"),
                           (self.results_tab, "Results and traces"),
                           (self.formulas_tab, "Equations"),
                           (self.sources_tab, "Sources and scope")):
            book.add(tab, text=title)
        self._build_params()
        self._build_channels()
        self._build_builder()
        self._build_custom()
        self._build_search()
        self._build_results()
        self._build_formulas()
        self._build_sources()
        foot = ttk.Frame(self, padding=(13, 5))
        foot.pack(fill="x")
        ttk.Label(foot, textvariable=self.status_var).pack(side="left")
        ttk.Label(foot, textvariable=self.progress_var).pack(side="right")
        self.progress = ttk.Progressbar(foot, maximum=100, length=220)
        self.progress.pack(side="right", padx=10)

    def _build_params(self):
        tab = self.params_tab
        ttk.Label(tab, text="Representative values and per-parameter bounds. Build joint searches on the next tab.",
                  foreground="#526070").pack(anchor="w", pady=(0, 10))
        columns = (("key", "Parameter", 130), ("value", "Baseline", 130),
                   ("low", "Lower", 120), ("high", "Upper", 120),
                   ("dist", "Distribution", 110), ("unit", "Unit", 110),
                   ("source", "Bounds", 140), ("status", "Channel", 80))
        self.params_tree = ttk.Treeview(tab, columns=[c[0] for c in columns],
                                        show="headings", selectmode="extended", height=16)
        for key, label, width in columns:
            self.params_tree.heading(key, text=label)
            self.params_tree.column(key, width=width)
        self.params_tree.pack(fill="both", expand=True)
        self.params_tree.bind("<Double-1>", lambda event: self.edit_parameter())
        buttons = ttk.Frame(tab)
        buttons.pack(fill="x", pady=7)
        ttk.Button(buttons, text="Edit selected value and bounds", command=self.edit_parameter).pack(side="left")
        ttk.Button(buttons, text="Reset to defaults", command=self.reset_parameters).pack(side="left", padx=10)
        fixed = ttk.LabelFrame(tab, text="Fixed constants without default search bounds", padding=7)
        fixed.pack(fill="x", pady=(10, 0))
        fixed_body = ttk.Frame(fixed)
        fixed_body.pack(fill="x")
        self.fixed_tree = ttk.Treeview(fixed_body, columns=("name", "value", "source"),
                                       show="headings", height=5)
        for key, label, width in (("name", "Constant", 170), ("value", "Value", 150),
                                  ("source", "Source", 180)):
            self.fixed_tree.heading(key, text=label)
            self.fixed_tree.column(key, width=width)
        self.fixed_tree.pack(side="left", fill="x", expand=True)
        fixed_scroll = ttk.Scrollbar(fixed_body, orient="vertical", command=self.fixed_tree.yview)
        fixed_scroll.pack(side="left", fill="y")
        self.fixed_tree.configure(yscrollcommand=fixed_scroll.set)
        self.fixed_tree.bind("<Double-1>", lambda event: self.edit_fixed())
        ttk.Button(fixed, text="Edit selected constant", command=self.edit_fixed).pack(anchor="w", pady=5)

    def _build_channels(self):
        tab = self.channels_tab
        ttk.Label(tab, text="Turn a channel off to set its conductance to zero in traces and searches.",
                  font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(tab, text="The representative value remains available when you turn it on again. "
                  "An off channel cannot be a search axis; affected searches are removed.",
                  foreground="#526070").pack(anchor="w", pady=(0, 14))
        self.channel_grid = ttk.Frame(tab)
        self.channel_grid.pack(anchor="w", fill="x")
        ttk.Button(tab, text="Turn all channels on", command=self.enable_all_channels).pack(
            anchor="w", pady=18)

    def _build_builder(self):
        tab = self.builder_tab
        ttk.Label(tab, text="Combine published channel modules into a custom single-cell model.",
                  font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 7))
        ttk.Label(tab, text="Select a channel, choose its source model, then add it. "
                  "The hybrid current sum is an exploratory construction, not a published model.",
                  foreground="#526070").pack(anchor="w", pady=(0, 9))
        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True)
        self.builder_tree = ttk.Treeview(frame, columns=("key", "channel", "included", "source"),
                                         show="headings", selectmode="browse", height=15)
        for key, label, width in (("key", "Parameter", 120), ("channel", "Channel", 220),
                                  ("included", "Included", 100), ("source", "Parameter source", 230)):
            self.builder_tree.heading(key, text=label)
            self.builder_tree.column(key, width=width)
        self.builder_tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.builder_tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.builder_tree.configure(yscrollcommand=scrollbar.set)
        self.builder_tree.bind("<<TreeviewSelect>>", self._builder_selected)
        controls = ttk.Frame(tab)
        controls.pack(fill="x", pady=9)
        ttk.Label(controls, text="Source").pack(side="left")
        self.builder_source_var = tk.StringVar()
        self.builder_source_box = ttk.Combobox(controls, textvariable=self.builder_source_var,
                                                state="readonly", width=25)
        self.builder_source_box.pack(side="left", padx=8)
        ttk.Button(controls, text="Add / change selected", command=self.add_module).pack(side="left")
        ttk.Button(controls, text="Remove selected", command=self.remove_module).pack(side="left", padx=9)
        ttk.Button(tab, text="Restore published model", command=self.reset_parameters).pack(
            anchor="w", pady=5)
        self.builder_status_var = tk.StringVar()
        ttk.Label(tab, textvariable=self.builder_status_var, foreground="#41536a").pack(
            anchor="w", pady=8)

    def _build_custom(self):
        tab = self.custom_tab
        ttk.Label(tab, text="Add hand-written current equations to the selected cell model.",
                  font=("Arial", 12, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(tab, text="Each gate can be instantaneous or an ODE with its own initial value. "
                  "New constants appear in Values and bounds and can join random or sweep searches. "
                  "Existing published equations are preserved unless you turn their channels off.",
                  wraplength=1000, foreground="#526070").pack(anchor="w", pady=(0, 12))
        self.custom_list = tk.Listbox(tab, height=12, exportselection=False)
        self.custom_list.pack(fill="both", expand=True)
        self.custom_list.bind("<Double-1>", lambda event: self.edit_custom())
        controls = ttk.Frame(tab)
        controls.pack(fill="x", pady=10)
        for label, action in (("New current", self.add_custom),
                              ("Edit selected", self.edit_custom),
                              ("Remove selected", self.remove_custom),
                              ("Save modules…", self.save_custom),
                              ("Load modules…", self.load_custom)):
            ttk.Button(controls, text=label, command=action).pack(side="left", padx=(0, 8))
        ttk.Label(tab, text="Current I is an intrinsic current density (µA/cm²): "
                  "dV/dt receives -I/C. Optional Na and Ca rates are entered explicitly. "
                  "These modules are experimental definitions authored by the user.",
                  wraplength=1000, foreground="#526070").pack(anchor="w", pady=8)

    def _custom_state_names(self):
        family = effective_model(self.model_var.get(), self.composition)["family"]
        if family in ("FNAN", "COMPOSED"):
            return ("Na_mM", "Ca_uM")
        return ("Na_mM",) if family == "NAN" else ("Ca_uM",)

    def _validate_custom(self, modules):
        spec = effective_model(self.model_var.get(), self.composition)
        compile_modules(modules, spec["parameters"], spec["fixed"], self._custom_state_names())

    def _set_custom(self, modules):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Running", "Finish the current calculation first.", parent=self)
            return
        self._validate_custom(modules)
        old_values, old_ranges = self.parameters, self.ranges
        spec = effective_model(self.model_var.get(), self.composition)
        defaults = module_parameters(modules)
        ranges = module_ranges(modules)
        previous_defaults = module_parameters(self.custom_modules)
        previous_ranges = module_ranges(self.custom_modules)
        self.custom_modules = copy.deepcopy(modules)
        self.parameters = {**{key: old_values[key] for key in spec["parameters"]},
                           **{key: (old_values[key] if key in old_values and
                                     previous_defaults.get(key) == value else value)
                              for key, value in defaults.items()}}
        self.ranges = {**{key: old_ranges[key] for key in spec["ranges"]},
                       **{key: (old_ranges[key] if key in old_ranges and
                                     previous_ranges.get(key) == value else value)
                          for key, value in ranges.items()}}
        self.points = {key: self.points.get(key, 100) for key in self.ranges}
        self.groups = []
        self.refresh_custom()
        self.refresh_params()
        self.refresh_groups()
        self._refresh_equations()
        self._refresh_sources(spec)
        self.status_var.set(f"{len(modules)} custom current(s); queued searches reset")

    def refresh_custom(self):
        self.custom_list.delete(0, "end")
        for module in self.custom_modules:
            kinds = ", ".join(f'{v["name"]}:{v["kind"]}' for v in module.get("variables", []))
            self.custom_list.insert("end", f'{module["name"]} | I = {module["current"]}'
                                    f' | {kinds} | {len(module.get("parameters", []))} parameters')

    def _edit_custom(self, index=None):
        before = self.custom_modules[index] if index is not None else None
        def validate(module):
            proposed = copy.deepcopy(self.custom_modules)
            if index is None:
                proposed.append(module)
            else:
                proposed[index] = module
            self._validate_custom(proposed)
        dialog = ModuleEditor(self, before, validate)
        self.wait_window(dialog)
        if dialog.result is not None:
            proposed = copy.deepcopy(self.custom_modules)
            if index is None:
                proposed.append(dialog.result)
            else:
                proposed[index] = dialog.result
            self._set_custom(proposed)

    def add_custom(self):
        self._edit_custom()

    def edit_custom(self):
        selected = self.custom_list.curselection()
        if selected:
            self._edit_custom(selected[0])

    def remove_custom(self):
        selected = self.custom_list.curselection()
        if selected:
            proposed = copy.deepcopy(self.custom_modules)
            proposed.pop(selected[0])
            self._set_custom(proposed)

    def save_custom(self):
        if not self.custom_modules:
            messagebox.showinfo("Modules", "Create a module first.", parent=self)
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".json",
                  filetypes=[("JSON", "*.json")], initialfile="custom_currents.json")
        if path:
            modules = copy.deepcopy(self.custom_modules)
            for module in modules:
                for param in module["parameters"]:
                    key = param["name"]
                    param["value"] = self.parameters[key]
                    param["low"], param["high"], param["distribution"], param["unit"] = self.ranges[key]
            Path(path).write_text(json.dumps({"format": "ChannelCircuitLab.custom.v1",
                  "base_model": self.model_var.get(), "modules": modules}, indent=2)+"\n",
                  encoding="utf-8")

    def load_custom(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("JSON", "*.json")])
        if path:
            try:
                payload = json.loads(Path(path).read_text(encoding="utf-8"))
                if payload.get("format") != "ChannelCircuitLab.custom.v1":
                    raise ValueError("Unsupported custom module file")
                self._set_custom(payload["modules"])
            except (ValueError, TypeError, KeyError, OSError) as exc:
                messagebox.showerror("Load modules", str(exc), parent=self)

    def _refresh_equations(self):
        base = equations_for(self.model_var.get(), self.disabled_channels, self.composition)
        if self.custom_modules:
            base += "\n\nUSER-DEFINED EXPERIMENTAL CURRENTS\n"
            for module in self.custom_modules:
                base += f'\n{module["name"]}\nI = {module["current"]}\n'
                for variable in module["variables"]:
                    notation = (f'd{variable["name"]}/dt' if variable["kind"] == "ode"
                                else variable["name"])
                    expression = variable["equation"]
                    if variable["kind"] == "ode" and variable.get("ode_form") == "relaxation":
                        expression = (f'({expression} - {variable["name"]}) / '
                                      f'({variable["tau"]})')
                    base += f'{notation} = {expression}'
                    if variable["kind"] == "ode":
                        base += f'  (initial: {variable["initial"]})'
                    base += "\n"
                base += f'dNa/dt += {module.get("na_rate", "0")}\n'
                base += f'dCa/dt += {module.get("ca_rate", "0")}\n'
        self._write_text(self.formulas_text, base)

    def _current_modules(self):
        if self.composition is not None:
            return dict(self.composition["channels"])
        name = self.model_var.get()
        return {key: name for key in available_channels(name)}

    def refresh_builder(self):
        previous = self.builder_tree.selection()
        self.builder_tree.delete(*self.builder_tree.get_children())
        chosen = self._current_modules()
        for key, label in CHANNEL_LABELS.items():
            self.builder_tree.insert("", "end", iid=key, values=(
                key, label, "Yes" if key in chosen else "No", chosen.get(key, "")))
        if previous and previous[0] in CHANNEL_LABELS:
            self.builder_tree.selection_set(previous[0])
        self.builder_status_var.set(
            f'{"Custom assembly" if self.composition is not None else "Published model"} | '
            f'{len(chosen)} selected channels | Na and Ca states in custom assemblies')

    def _builder_selected(self, event=None):
        selected = self.builder_tree.selection()
        if not selected:
            return
        key = selected[0]
        sources = channel_sources(key)
        self.builder_source_box.configure(values=sources)
        self.builder_source_var.set(self._current_modules().get(key, sources[0]))

    def _set_composition(self, modules):
        if self.worker and self.worker.is_alive():
            self.status_var.set("Wait for the current calculation before changing modules")
            self.refresh_builder()
            return
        try:
            spec = compose_model(self.model_var.get(),
                                 {"channels": modules})
            compile_modules(self.custom_modules, spec["parameters"], spec["fixed"],
                            ("Na_mM", "Ca_uM"))
        except ValueError as exc:
            messagebox.showerror("Build model", str(exc), parent=self)
            self.refresh_builder()
            return
        old_modules = self._current_modules()
        old_parameters, old_ranges, old_points = self.parameters, self.ranges, self.points
        def origin(key, selected):
            return selected.get("gUNaV") if key in ("x", "y") else selected.get(key)
        self.parameters = {key: (old_parameters[key] if key in old_parameters and
                                 origin(key, old_modules) == origin(key, modules)
                                 else value)
                           for key, value in spec["parameters"].items()}
        self.parameters.update({key: old_parameters.get(key, value) for key, value in
                                module_parameters(self.custom_modules).items()})
        self.ranges = {key: (old_ranges[key] if key in old_ranges and
                             origin(key, old_modules) == origin(key, modules)
                             else domain)
                       for key, domain in spec["ranges"].items()}
        self.ranges.update({key: old_ranges.get(key, domain) for key, domain in
                            module_ranges(self.custom_modules).items()})
        self.points = {key: old_points.get(key, 100) for key in self.ranges}
        self.fixed_values = {key: self.fixed_values.get(key, value)
                             for key, value in spec["fixed"].items()}
        self.composition = spec["composition"]
        self.disabled_channels &= set(modules)
        self.groups = []
        self.sweep_basis_var.set("edited")
        self.sweep_basis_box.configure(values=("edited",))
        self.refresh_builder()
        self.refresh_channels()
        self.refresh_params()
        self.refresh_fixed()
        self.refresh_groups()
        self._refresh_equations()
        self._refresh_sources(spec)
        self.status_var.set(f"Custom model: {len(modules)} channels; searches reset")

    def add_module(self):
        selected = self.builder_tree.selection()
        if not selected:
            return
        key = selected[0]
        source = self.builder_source_var.get()
        if source not in channel_sources(key):
            messagebox.showerror("Build model", "Choose a listed source model", parent=self)
            return
        modules = self._current_modules()
        modules[key] = source
        self._set_composition(modules)

    def remove_module(self):
        selected = self.builder_tree.selection()
        if not selected:
            return
        modules = self._current_modules()
        modules.pop(selected[0], None)
        self._set_composition(modules)

    def refresh_channels(self):
        for widget in self.channel_grid.winfo_children():
            widget.destroy()
        self.channel_vars = {}
        for i, (key, label) in enumerate(available_channels(
                self.model_var.get(), self.composition).items()):
            variable = tk.BooleanVar(value=key not in self.disabled_channels)
            self.channel_vars[key] = variable
            ttk.Checkbutton(self.channel_grid, text=f"{label}  ({key})", variable=variable,
                            command=lambda name=key: self.toggle_channel(name)).grid(
                                row=i//2, column=i%2, sticky="w", padx=(0, 48), pady=8)

    def toggle_channel(self, key):
        if self.worker and self.worker.is_alive():
            self.channel_vars[key].set(key not in self.disabled_channels)
            return
        if self.channel_vars[key].get():
            self.disabled_channels.discard(key)
        else:
            self.disabled_channels.add(key)
            self.groups = [group for group in self.groups if key not in group["parameters"]]
            self.search_params_tree.selection_remove(key)
        self.refresh_groups()
        self.refresh_params()
        self._refresh_equations()
        self.status_var.set(f"{key}: {'off (g=0)' if key in self.disabled_channels else 'on'}")

    def enable_all_channels(self):
        if self.worker and self.worker.is_alive():
            return
        self.disabled_channels.clear()
        for variable in self.channel_vars.values():
            variable.set(True)
        self.refresh_params()
        self._refresh_equations()
        self.status_var.set("All channels on")

    def _build_search(self):
        tab = self.search_tab
        ttk.Label(tab, text="1 Select parameters  →  2 Set bounds and draws / levels  →  3 Choose random or sweep",
                  foreground="#526070").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        columns = (("key", "Parameter", 140), ("value", "Baseline", 115),
                   ("low", "Lower", 110), ("high", "Upper", 110),
                   ("dist", "Distribution", 100), ("points", "Draws/levels", 110),
                   ("unit", "Unit", 90), ("status", "Channel", 75))
        self.search_params_tree = ttk.Treeview(tab, columns=[x[0] for x in columns],
                                               show="headings", selectmode="extended", height=9)
        for key, label, width in columns:
            self.search_params_tree.heading(key, text=label)
            self.search_params_tree.column(key, width=width)
        self.search_params_tree.grid(row=1, column=0, columnspan=4, sticky="nsew")
        self.search_params_tree.bind("<Double-1>", lambda event: self.edit_search_parameter())
        self.search_params_tree.bind("<<TreeviewSelect>>", self.refresh_search_preview)
        ttk.Button(tab, text="Edit selected parameter",
                   command=self.edit_search_parameter).grid(row=2, column=0, columnspan=2,
                                                             sticky="w", pady=6)
        all_controls = ttk.Frame(tab)
        all_controls.grid(row=2, column=2, columnspan=2, sticky="e")
        ttk.Label(all_controls, text="All draws / levels").pack(side="left", padx=5)
        ttk.Entry(all_controls, textvariable=self.all_points_var, width=7).pack(side="left", padx=5)
        ttk.Button(all_controls, text="Apply to all", command=self.apply_all_points).pack(side="left")
        ttk.Label(tab, text="Search mode").grid(row=3, column=0, sticky="w", pady=8)
        kind_box = ttk.Combobox(tab, textvariable=self.kind_var, values=("random", "sweep"),
                                state="readonly", width=17)
        kind_box.grid(row=3, column=1, sticky="w")
        kind_box.bind("<<ComboboxSelected>>", self.refresh_search_preview)
        ttk.Label(tab, text="Sweep bounds").grid(row=3, column=2, sticky="e", padx=8)
        self.sweep_basis_box = ttk.Combobox(tab, textvariable=self.sweep_basis_var,
                                             values=("edited", "paper"),
                                             state="readonly", width=13)
        self.sweep_basis_box.grid(row=3, column=3, sticky="w")
        ttk.Label(tab, textvariable=self.search_preview_var,
                  foreground="#41536a").grid(row=4, column=0, columnspan=4, sticky="w", pady=8)
        ttk.Button(tab, text="Add selected parameters as a search", command=self.add_group).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 8))
        self.groups_list = tk.Listbox(tab, width=100, height=5, exportselection=False)
        self.groups_list.grid(row=6, column=0, columnspan=4, sticky="nsew")
        ttk.Button(tab, text="Remove selected search", command=self.remove_group).grid(row=7, column=0, sticky="w", pady=5)
        ttk.Separator(tab).grid(row=8, column=0, columnspan=4, sticky="ew", pady=7)
        ttk.Label(tab, text=f"Logical CPUs detected: {self.cpus}").grid(row=9, column=0, sticky="w", pady=6)
        ttk.Label(tab, text="Worker processes").grid(row=10, column=0, sticky="w", pady=6)
        tk.Spinbox(tab, from_=1, to=self.cpus, textvariable=self.workers_var, width=8).grid(row=10, column=1,
                                                                                              sticky="w")
        ttk.Label(tab, text="Sampling seed is generated automatically and saved with results.",
                  foreground="#526070").grid(row=11, column=0, columnspan=3, sticky="w", pady=5)
        ttk.Label(tab, text="Output folder").grid(row=12, column=0, sticky="w", pady=6)
        ttk.Entry(tab, textvariable=self.output_var, width=65).grid(row=12, column=1, columnspan=2, sticky="ew")
        ttk.Button(tab, text="Browse…", command=self.choose_output).grid(row=12, column=3, padx=6)
        self.start_button = ttk.Button(tab, text="Run search", command=self.start_search)
        self.start_button.grid(row=13, column=0, sticky="w", pady=10)
        self.stop_button = ttk.Button(tab, text="Stop", command=self.stop_search, state="disabled")
        self.stop_button.grid(row=13, column=1, sticky="w", pady=10)
        tab.columnconfigure(2, weight=1)
        tab.rowconfigure(1, weight=2)
        tab.rowconfigure(6, weight=1)

    def _build_results(self):
        tab = self.results_tab
        ttk.Label(tab, textvariable=self.info_var, font=("Helvetica", 11, "bold")).pack(anchor="w")
        buttons = ttk.Frame(tab)
        buttons.pack(fill="x", pady=8)
        ttk.Button(buttons, text="Open result folder", command=self.open_output).pack(side="left")
        ttk.Button(buttons, text="Open previous search", command=self.load_output).pack(side="left", padx=8)
        ttk.Button(buttons, text="Compute selected trace", command=self.inspect_selected).pack(side="left")
        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True)
        left = ttk.Frame(frame)
        left.pack(side="left", fill="y", padx=(0, 12))
        self.results_tree = ttk.Treeview(left, columns=("cell", "group", "index", "choices",
                                                 "label", "hz", "spikes"),
                                         show="headings", height=20)
        for key, label, width in (("cell", "Cell", 42), ("group", "Group", 85),
                                  ("index", "Run", 55), ("choices", "Candidate indices", 190),
                                  ("label", "Class", 105), ("hz", "Peak Hz", 70),
                                  ("spikes", "Spikes/s", 75)):
            self.results_tree.heading(key, text=label)
            self.results_tree.column(key, width=width)
        self.results_tree.pack(fill="y", expand=True)
        self.results_tree.bind("<<TreeviewSelect>>", self.show_selected)
        self.image_label = ttk.Label(frame, text="Run a baseline or select a search result",
                                     anchor="center")
        self.image_label.pack(side="left", fill="both", expand=True)
        self.loaded_rows = []

    def _build_sources(self):
        tab = self.sources_tab
        self.source_text = tk.Text(tab, wrap="word", state="disabled", font=("Helvetica", 12))
        self.source_text.pack(fill="both", expand=True)

    def _build_formulas(self):
        ttk.Label(self.formulas_tab, text="Membrane, ion concentration, current and gate equations",
                  font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 8))
        frame = ttk.Frame(self.formulas_tab)
        frame.pack(fill="both", expand=True)
        self.formulas_text = tk.Text(frame, wrap="word", state="disabled",
                                     font=("Menlo", 11))
        self.formulas_text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.formulas_text.yview)
        scroll.pack(side="right", fill="y")
        self.formulas_text.configure(yscrollcommand=scroll.set)

    def load_model(self):
        validate_assignment(self.role_var.get(), self.model_var.get())
        self.loaded_model = self.model_var.get()
        spec = get_model(self.model_var.get())
        self.composition = None
        self.custom_modules = []
        self.parameters = copy.deepcopy(spec["parameters"])
        self.fixed_values = copy.deepcopy(spec["fixed"])
        self.ranges = copy.deepcopy(spec["ranges"])
        self.points = {key: 100 for key in self.ranges}
        self.disabled_channels = set()
        self.groups = []
        self.sweep_basis_box.configure(values=("edited", "paper"))
        self.sweep_basis_var.set("edited")
        self.refresh_builder()
        self.refresh_custom()
        self.refresh_channels()
        self.refresh_params()
        self.refresh_groups()
        self.refresh_fixed()
        self._refresh_equations()
        self._refresh_sources(spec)
        self.status_var.set(f"{self.role_var.get()} / {self.model_var.get()} | CPUs: {self.cpus}")

    def _refresh_sources(self, spec):
        note = ("Custom channel assembly: parameter sources are recorded in outputs. "
                "The summed hybrid and its behavior are not "
                "validated published models. Custom sweeps use edited bounds only."
                if self.composition is not None else
                "Published equation set. Edited values, bounds and channel switches "
                "are saved as experimental settings.")
        self._write_text(self.source_text,
            "Equations: " + spec["equations"] + "\n\nRepresentative values: " + spec["baseline"]
            + "\n\nRanges and sampling: " + spec["search"] + "\n\n"
            + "The I role is a research assignment, not a cell-type-specific fit. "
            + "Single cells are simulated; automatic SWO labels require visual review.\n\n"
            + note + ("\n\nUser-authored experimental currents: " +
                      ", ".join(module["name"] for module in self.custom_modules)
                      if self.custom_modules else ""))

    def on_role_selected(self, event=None):
        if self.worker and self.worker.is_alive():
            self.role_var.set(self.active_role)
            return
        self.saved_models[self.active_role] = self.loaded_model
        self.active_role = self.role_var.get()
        self.model_box.configure(values=CELL_ROLE_MODELS[self.active_role])
        self.model_var.set(self.saved_models[self.active_role])
        self.load_model()

    def on_model_selected(self, event=None):
        if self.worker and self.worker.is_alive():
            self.model_var.set(self.loaded_model)
            return
        self.saved_models[self.active_role] = self.model_var.get()
        self.load_model()

    @staticmethod
    def _write_text(widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", value)
        widget.configure(state="disabled")

    def refresh_params(self):
        self.params_tree.delete(*self.params_tree.get_children())
        original = {**effective_model(self.model_var.get(), self.composition)["ranges"],
                    **module_ranges(self.custom_modules)}
        channels = available_channels(self.model_var.get(), self.composition)
        for key, domain in self.ranges.items():
            self.params_tree.insert("", "end", iid=key, values=(
                key, f"{self.parameters[key]:.9g}", f"{domain[0]:.9g}",
                f"{domain[1]:.9g}", domain[2], domain[3],
                "Edited" if domain != original[key] else
                "Custom" if key in module_parameters(self.custom_modules) else "Default bounds",
                "Off" if key in self.disabled_channels else "On" if key in channels else
                "Custom" if key in module_parameters(self.custom_modules) else "—"))
        self.refresh_search_params()

    def refresh_search_params(self):
        selected = set(self.search_params_tree.selection())
        self.search_params_tree.delete(*self.search_params_tree.get_children())
        channels = available_channels(self.model_var.get(), self.composition)
        for key, (low, high, dist, unit) in self.ranges.items():
            self.search_params_tree.insert("", "end", iid=key, values=(
                key, f"{self.parameters[key]:.9g}", f"{low:.9g}",
                f"{high:.9g}", dist, self.points[key], unit,
                "Off" if key in self.disabled_channels else "On" if key in
                channels else "Custom" if key in module_parameters(self.custom_modules) else "—"))
        self.search_params_tree.selection_set(tuple(selected & set(self.ranges)))
        self.refresh_search_preview()

    def refresh_search_preview(self, event=None):
        if not hasattr(self, "search_params_tree"):
            return
        keys = self.search_params_tree.selection()
        if not keys:
            self.search_preview_var.set("Select parameters to see the condition count")
            return
        levels = [self.points[key] for key in keys]
        if self.kind_var.get() == "sweep":
            self.search_preview_var.set(
                f"{' × '.join(map(str, levels))} = {math.prod(levels):,} conditions")
        elif len(set(levels)) != 1:
            self.search_preview_var.set("Random requires equal draws per parameter")
        else:
            self.search_preview_var.set(f"{levels[0]:,} paired random draws = {levels[0]:,} conditions")

    def apply_all_points(self):
        try:
            count = int(self.all_points_var.get())
            if count < 1:
                raise ValueError("Enter a positive whole number")
            new_groups = copy.deepcopy(self.groups)
            for group in new_groups:
                group["points"] = {key: count for key in group["parameters"]}
                group["samples"] = (count if group["kind"] == "random"
                                    else count ** len(group["parameters"]))
        except ValueError as exc:
            messagebox.showerror("Draws / levels", str(exc), parent=self)
            return
        self.points = {key: count for key in self.points}
        self.groups = new_groups
        self.refresh_params()
        self.refresh_groups()
        self.status_var.set(f"{count} draws / levels applied to all parameters and searches")

    def edit_search_parameter(self):
        selected = self.search_params_tree.selection()
        if not selected:
            return
        key = selected[0]
        dialog = ParameterDialog(self, key, self.parameters[key], self.ranges[key],
                                 self.points[key], key in module_parameters(self.custom_modules))
        self.wait_window(dialog)
        if dialog.result:
            value, low, high, dist, points = dialog.result
            self.parameters[key] = value
            self.ranges[key] = (low, high, dist, self.ranges[key][3])
            self.points[key] = points
            self.refresh_params()
            self.search_params_tree.selection_set(key)

    def refresh_fixed(self):
        self.fixed_tree.delete(*self.fixed_tree.get_children())
        published = effective_model(self.model_var.get(), self.composition)["fixed"]
        for key, value in self.fixed_values.items():
            origin = "Edited" if value != published[key] else "Published value"
            if key == "VL" and self.model_var.get().startswith("Sato"):
                origin = "From Tatsuki" if value == published[key] else "Edited from Tatsuki"
            self.fixed_tree.insert("", "end", iid=key, values=(key, f"{value:.9g}", origin))

    def edit_fixed(self):
        selected = self.fixed_tree.selection()
        if not selected:
            return
        key = selected[0]
        value = simpledialog.askfloat(key, f"{key} value (published: "
                                      f'{effective_model(self.model_var.get(), self.composition)["fixed"][key]:.9g})',
                                      initialvalue=self.fixed_values[key], parent=self)
        if value is None:
            return
        positive = {"C", "A_mm2", "Kd_uM", "Ke_uM", "Kf", "tau_hA",
                    "alphaCa", "alphaNa_uM_per_nA_ms", "tauAMPA",
                    "tau_sNMDA", "tau_xNMDA", "tauGABA"}
        if not math.isfinite(value) or key in positive and value <= 0:
            messagebox.showerror("Constant", "Enter a finite value. Times, capacitance and concentrations must be positive.", parent=self)
            return
        self.fixed_values[key] = value
        self.refresh_fixed()
        self.fixed_tree.selection_set(key)

    def edit_parameter(self):
        selected = self.params_tree.selection()
        if not selected:
            return
        key = selected[0]
        dialog = ParameterDialog(self, key, self.parameters[key], self.ranges[key],
                                 allow_negative=key in module_parameters(self.custom_modules))
        self.wait_window(dialog)
        if dialog.result:
            value, low, high, dist, _ = dialog.result
            self.parameters[key] = value
            self.ranges[key] = (low, high, dist, self.ranges[key][3])
            self.refresh_params()
            self.params_tree.selection_set(key)

    def reset_parameters(self):
        if messagebox.askyesno("Reset to defaults", "Reset the current model values, bounds and searches?", parent=self):
            self.load_model()

    def add_group(self):
        selected = tuple(self.search_params_tree.selection())
        kind = self.kind_var.get()
        if not selected:
            messagebox.showerror("Combination", "Select one or more parameters.", parent=self)
            return
        if set(selected) & self.disabled_channels:
            messagebox.showerror("Combination", "Turn on a channel before using it as a search axis.", parent=self)
            return
        basis = self.sweep_basis_var.get()
        if (kind == "sweep" and basis != "edited" and
                (self.composition is not None or set(selected) &
                 set(module_parameters(self.custom_modules)))):
            messagebox.showerror("Sweep", "Custom parameters use edited bounds only.", parent=self)
            return
        if (kind == "sweep" and basis == "paper" and any(
                key not in ("x", "y") and self.parameters[key] == 0 for key in selected)):
            messagebox.showerror("Sweep", "A zero baseline needs edited bounds.", parent=self)
            return
        try:
            points = {key: self.points[key] for key in selected}
            if kind == "random" and len(set(points.values())) != 1:
                raise ValueError("Random requires equal draw counts for every selected parameter.")
            samples = next(iter(points.values())) if kind == "random" else math.prod(points.values())
            if samples < 1:
                raise ValueError("Choose a positive number of conditions")
        except ValueError as exc:
            messagebox.showerror("Condition count", str(exc), parent=self)
            return
        number = 1
        while f"group_{number}" in {g["name"] for g in self.groups}:
            number += 1
        name = f"group_{number}"
        self.groups.append({"name": name, "kind": kind, "basis": basis, "samples": samples,
                            "parameters": list(selected),
                            "points": points,
                            "ranges": {key: self.ranges[key] for key in selected}})
        self.refresh_groups()

    def refresh_groups(self):
        self.groups_list.delete(0, "end")
        for g in self.groups:
            basis = f' ({g.get("basis", "paper")})' if g["kind"] == "sweep" else ""
            bounds = ", ".join(f'{key}[{g.get("ranges", self.ranges)[key][0]:g}–'
                               f'{g.get("ranges", self.ranges)[key][1]:g}]'
                               for key in g["parameters"])
            grid = " × ".join(str(g.get("points", {}).get(key, g["samples"]))
                              for key in g["parameters"])
            levels = (f'{group_total(g):,} paired random draws' if g["kind"] == "random"
                      else f'{grid} sweep levels')
            self.groups_list.insert("end", f'{g["name"]}: ({", ".join(g["parameters"])})'
                                    f' | {levels} | {g["kind"]}{basis} | '
                                    f'{group_total(g):,} conditions | {bounds}')

    def remove_group(self):
        selected = self.groups_list.curselection()
        if selected:
            self.groups.pop(selected[0])
            self.refresh_groups()

    def choose_output(self):
        path = filedialog.askdirectory(parent=self, initialdir=str(Path.home()))
        if path:
            self.output_var.set(path)

    def _next_output(self, suffix):
        root = Path(self.output_var.get()).expanduser()
        if not self.output_var.get().strip():
            raise ValueError("Select an output folder")
        return root/(datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + suffix)

    def _begin(self, target, args):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Running", "Wait for the current calculation to finish.", parent=self)
            return
        self.cancel.clear()
        self.start_button.configure(state="disabled")
        self.baseline_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress["value"] = 0
        self.progress_var.set("0 %")
        self.worker = threading.Thread(target=target, args=args, daemon=True)
        self.worker.start()

    def run_baseline(self):
        try:
            out = self._next_output("baseline")
            self._begin(self._baseline_worker, (self.model_var.get(),
                                                self.role_var.get(),
                                                copy.deepcopy(self.parameters),
                                                copy.deepcopy(self.fixed_values),
                                                tuple(sorted(self.disabled_channels)),
                                                copy.deepcopy(self.composition),
                                                copy.deepcopy(self.custom_modules), out))
        except ValueError as exc:
            messagebox.showerror("Baseline trace", str(exc), parent=self)

    def _baseline_worker(self, model, cell_role, values, fixed, disabled, composition,
                         custom_modules, out):
        try:
            result = simulate(model, values, fixed_overrides=fixed, stop=self.cancel,
                              progress=lambda n, total: self.events.put(("wave_progress", (n, total))),
                              disabled_channels=disabled, composition=composition,
                              custom_modules=custom_modules)
            result["cell_role"] = cell_role
            save_result(result, out)
            self.events.put(("done", (out, "Baseline completed")))
        except Exception:
            self.events.put(("error", traceback.format_exc()))

    def start_search(self):
        try:
            if not self.groups:
                raise ValueError("Select parameters and add a search on the Combinations tab")
            total = sum(group_total(group) for group in self.groups)
            workers = int(self.workers_var.get())
            if not 1 <= workers <= self.cpus:
                raise ValueError(f"Worker count must be between 1 and {self.cpus}")
            out = self._next_output("search")
            self._begin(self._search_worker,
                (self.model_var.get(), self.role_var.get(),
                 copy.deepcopy(self.parameters),
                 copy.deepcopy(self.fixed_values), copy.deepcopy(self.ranges),
                 copy.deepcopy(self.groups), workers,
                 tuple(sorted(self.disabled_channels)),
                 copy.deepcopy(self.composition),
                 copy.deepcopy(self.custom_modules), out))
            self.progress_var.set(f"0 / {total:,}")
            self.book.select(self.results_tab)
        except ValueError as exc:
            messagebox.showerror("Search", str(exc), parent=self)

    def _search_worker(self, model, cell_role, values, fixed, ranges, groups,
                       workers, disabled, composition, custom_modules, out):
        try:
            run_search(model, ranges, groups, out, workers=workers,
                baseline_parameters=values, fixed_overrides=fixed, cell_role=cell_role,
                disabled_channels=disabled, composition=composition,
                custom_modules=custom_modules,
                stop=self.cancel,
                progress=lambda n, total, row: self.events.put(("progress", (n, total, row))))
            self.events.put(("done", (out, "Search stopped" if self.cancel.is_set() else "Search completed")))
        except Exception:
            self.events.put(("error", traceback.format_exc()))

    def stop_search(self):
        self.cancel.set()
        self.status_var.set("Stopping active calculations…")

    def poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    n, total, row = payload
                    self.progress["value"] = 100*n/total
                    self.progress_var.set(f"{n:,} / {total:,}")
                    self.status_var.set(f'Running: {row["group"]} #{row["index"]} → {row["label"]}')
                elif kind == "wave_progress":
                    n, total = payload
                    self.progress["value"] = 100*n/total
                    self.progress_var.set(f"{100*n/total:.0f} %")
                    self.status_var.set(f"Computing trace: {n/1000:g} / {total/1000:g} s")
                elif kind == "done":
                    self.start_button.configure(state="normal")
                    self.baseline_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.status_var.set(payload[1])
                    self.show_output(payload[0])
                    self.book.select(self.results_tab)
                elif kind == "inspect":
                    self.start_button.configure(state="normal")
                    self.baseline_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self._display_image(payload/"trace.pdf")
                    self.status_var.set("Selected trace displayed")
                elif kind == "error":
                    self.start_button.configure(state="normal")
                    self.baseline_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.status_var.set("Computation error")
                    messagebox.showerror("Computation error", payload[-4000:], parent=self)
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def show_output(self, path):
        self.last_output = Path(path)
        summary = self.last_output/"summary.csv"
        self.results_tree.delete(*self.results_tree.get_children())
        if summary.exists():
            priority = []
            ordinary = []
            completed = 0
            hits = 0
            with summary.open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    completed += 1
                    if row["label"] == "SWO_candidate":
                        hits += 1
                    if row["label"] in ("SWO_candidate", "SWO_few_spikes", "ERROR"):
                        if len(priority) < 500:
                            priority.append(row)
                    elif len(ordinary) < 500:
                        ordinary.append(row)
            self.visible_rows = (priority + ordinary[:max(0, 500-len(priority))])[:500]
            for i, row in enumerate(self.visible_rows):
                self.results_tree.insert("", "end", iid=str(i), values=(
                    row.get("cell_role", "Unassigned"), row["group"], row["index"],
                    row.get("choice_indices", ""), row["label"],
                    row["peak_hz"], row["spikes_per_s"]))
            self.info_var.set(f"{path} | {completed:,} completed; {hits:,} SWO candidates "
                              "(visual review required)")
            self.image_label.configure(image="", text="Select a condition to view its trace")
        else:
            self.visible_rows = []
            self.info_var.set(str(path))
            self._display_image(self.last_output/"trace.pdf")

    def _display_image(self, path):
        if not path.exists():
            self.image_label.configure(image="", text="No saved plot. Compute the selected trace to view it.")
            return
        document = pdfium.PdfDocument(str(path))
        try:
            page = document[0]
            try:
                img = page.render(scale=1.6).to_pil().copy()
            finally:
                page.close()
        finally:
            document.close()
        img.thumbnail((700, 540), Image.Resampling.LANCZOS)
        self.preview = ImageTk.PhotoImage(img)
        self.image_label.configure(image=self.preview, text="")

    def show_selected(self, event=None):
        selected = self.results_tree.selection()
        if not selected or self.last_output is None:
            return
        row = self.visible_rows[int(selected[0])]
        if row.get("trace_path"):
            self._display_image(self.last_output/row["trace_path"]/"trace.pdf")
        else:
            self.image_label.configure(image="", text="Click Compute selected trace to view it")

    def inspect_selected(self):
        selected = self.results_tree.selection()
        if not selected or self.last_output is None:
            return
        if self.worker and self.worker.is_alive():
            return
        row = self.visible_rows[int(selected[0])]
        try:
            model = row["model"]
            config = json.loads((self.last_output/"search_config.json").read_text(encoding="utf-8"))
            composition = config.get("composition")
            custom_modules = config.get("custom_modules", [])
            names = {**effective_model(model, composition)["parameters"],
                     **module_parameters(custom_modules)}
            values = {k: float(row[k]) for k in names}
            fixed = config["fixed_values"]
            disabled = tuple(config.get("disabled_channels", ()))
            cell_role = row.get("cell_role") or config.get("cell_role", "E")
            out = self.last_output/row["group"]/f'candidate_{int(row["index"]):06d}'
            self._begin(self._inspect_worker,
                        (model, cell_role, values, fixed, disabled, composition,
                         custom_modules, out))
        except (ValueError, KeyError, OSError) as exc:
            messagebox.showerror("Trace", str(exc), parent=self)

    def _inspect_worker(self, model, cell_role, values, fixed, disabled, composition,
                        custom_modules, out):
        try:
            result = simulate(model, values, fixed_overrides=fixed, stop=self.cancel,
                              progress=lambda n, total: self.events.put(("wave_progress", (n, total))),
                              disabled_channels=disabled, composition=composition,
                              custom_modules=custom_modules)
            result["cell_role"] = cell_role
            save_result(result, out)
            self.events.put(("inspect", out))
        except Exception:
            self.events.put(("error", traceback.format_exc()))

    def open_output(self):
        if self.last_output:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(self.last_output)])
            else:
                import webbrowser
                webbrowser.open(self.last_output.as_uri())

    def load_output(self):
        path = filedialog.askdirectory(parent=self, title="Result folder")
        if path:
            self.show_output(path)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    PaperApp().mainloop()
