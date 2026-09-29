"""Tkinter forms for user-authored current modules."""
from __future__ import annotations

import copy
import tkinter as tk
from tkinter import messagebox, ttk


class FormDialog(tk.Toplevel):
    def __init__(self, parent, title, fields, initial=None):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.grab_set()
        self.result = None
        self.resizable(False, False)
        self.values = {}
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        for row, (key, label, default, choices) in enumerate(fields):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=5, pady=5)
            value = tk.StringVar(value=str((initial or {}).get(key, default)))
            self.values[key] = value
            widget = (ttk.Combobox(body, values=choices, state="readonly", textvariable=value,
                                   width=30) if choices else ttk.Entry(body, textvariable=value, width=34))
            widget.grid(row=row, column=1, sticky="ew", padx=5, pady=5)
        ttk.Button(body, text="OK", command=self.save).grid(
            row=len(fields), column=1, sticky="e", pady=8)
        self.bind("<Escape>", lambda event: self.destroy())
        self.bind("<Return>", lambda event: self.save())

    def save(self):
        self.result = {key: value.get().strip() for key, value in self.values.items()}
        self.destroy()


class ModuleEditor(tk.Toplevel):
    def __init__(self, parent, initial, validate):
        super().__init__(parent)
        self.title("Custom current equations")
        self.geometry("1000x760")
        self.minsize(830, 630)
        self.transient(parent)
        self.grab_set()
        self.result = None
        self.validate_module = validate
        self.data = copy.deepcopy(initial or {
            "name": "ChannelX", "current": "gX * m^3 * (V - EX)",
            "na_rate": "0", "ca_rate": "0",
            "variables": [{"name": "m", "kind": "instant",
                           "equation": "sigmoid((V - halfX) / slopeX)", "initial": 0}],
            "parameters": [
                {"name": "gX", "value": 0.1, "low": 0.01, "high": 100,
                 "distribution": "log", "unit": "mS/cm²"},
                {"name": "EX", "value": 55, "low": 40, "high": 70,
                 "distribution": "uniform", "unit": "mV"},
                {"name": "halfX", "value": -35, "low": -60, "high": -10,
                 "distribution": "uniform", "unit": "mV"},
                {"name": "slopeX", "value": 7, "low": 2, "high": 15,
                 "distribution": "uniform", "unit": "mV"},
            ]})
        self.name_var = tk.StringVar(value=self.data["name"])
        self.current_var = tk.StringVar(value=self.data["current"])
        self.na_var = tk.StringVar(value=self.data.get("na_rate", "0"))
        self.ca_var = tk.StringVar(value=self.data.get("ca_rate", "0"))
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Experimental module | current density in µA/cm²",
                  font=("Arial", 13, "bold")).pack(anchor="w")
        ttk.Label(body, text="V in mV; t in ms; Na in mM; Ca in µM. "
                  "Use explicit * and parentheses. ^ is exponentiation. Existing model parameters "
                  "(for example x and y in Sato models) can be referenced directly.",
                  wraplength=930, foreground="#526070").pack(anchor="w", pady=(3, 10))
        for title, variable in (("Module name", self.name_var),
                                ("Current I (µA/cm²)", self.current_var),
                                ("Extra dNa/dt (mM/ms); I is the custom current", self.na_var),
                                ("Extra dCa/dt (µM/ms); I is the custom current", self.ca_var)):
            line = ttk.Frame(body)
            line.pack(fill="x", pady=3)
            ttk.Label(line, text=title, width=46).pack(side="left")
            ttk.Entry(line, textvariable=variable).pack(side="left", fill="x", expand=True)
        ttk.Label(body, text="Variables: instant gives a value. ODE relaxation uses "
                  "d(gate)/dt = (target - gate) / tau; direct derivative uses the entered d(gate)/dt.",
                  foreground="#526070").pack(anchor="w", pady=(10, 3))
        self.variables = ttk.Treeview(body, columns=("name", "kind", "equation", "initial"),
                                      show="headings", height=5, selectmode="browse")
        for key, label, width in (("name", "Name", 110), ("kind", "Mode / ODE form", 155),
                                  ("equation", "Evaluated equation", 515),
                                  ("initial", "Initial", 90)):
            self.variables.heading(key, text=label)
            self.variables.column(key, width=width)
        self.variables.pack(fill="both", expand=True)
        self.variables.bind("<Double-1>", lambda event: self.edit_variable())
        row = ttk.Frame(body)
        row.pack(fill="x", pady=4)
        for label, callback in (("Add variable", self.add_variable),
                                ("Edit selected", self.edit_variable),
                                ("Remove selected", self.remove_variable)):
            ttk.Button(row, text=label, command=callback).pack(side="left", padx=(0, 8))
        ttk.Label(body, text="Parameters: baseline value and bounds appear in the search tab.",
                  foreground="#526070").pack(anchor="w", pady=(8, 3))
        self.parameters = ttk.Treeview(body, columns=("name", "value", "low", "high", "dist", "unit"),
                                       show="headings", height=5, selectmode="browse")
        for key, label, width in (("name", "Name", 110), ("value", "Baseline", 110),
                                  ("low", "Lower", 100), ("high", "Upper", 100),
                                  ("dist", "Distribution", 110), ("unit", "Unit", 110)):
            self.parameters.heading(key, text=label)
            self.parameters.column(key, width=width)
        self.parameters.pack(fill="both", expand=True)
        self.parameters.bind("<Double-1>", lambda event: self.edit_parameter())
        row = ttk.Frame(body)
        row.pack(fill="x", pady=4)
        for label, callback in (("Add parameter", self.add_parameter),
                                ("Edit selected", self.edit_parameter),
                                ("Remove selected", self.remove_parameter)):
            ttk.Button(row, text=label, command=callback).pack(side="left", padx=(0, 8))
        ttk.Button(body, text="Validate and save module", command=self.save).pack(
            anchor="e", pady=8)
        self.refresh()

    def refresh(self):
        self.variables.delete(*self.variables.get_children())
        self.parameters.delete(*self.parameters.get_children())
        for index, variable in enumerate(self.data["variables"]):
            kind = variable["kind"]
            form = variable.get("ode_form", "derivative") if kind == "ode" else ""
            expression = variable["equation"]
            if kind == "ode" and form == "relaxation":
                expression = (f'({expression} - {variable["name"]}) / '
                              f'({variable.get("tau", "?")})')
            self.variables.insert("", "end", iid=str(index), values=(
                variable["name"], f"{kind} / {form}" if form else kind, expression,
                variable.get("initial", "") if kind == "ode" else "—"))
        for index, parameter in enumerate(self.data["parameters"]):
            self.parameters.insert("", "end", iid=str(index), values=(
                parameter["name"], parameter["value"], parameter["low"], parameter["high"],
                parameter["distribution"], parameter.get("unit", "")))

    def _edit(self, target, fields, index=None):
        initial = copy.deepcopy(self.data[target][index]) if index is not None else None
        if target == "variables" and initial is not None and initial.get("kind") == "ode":
            initial.setdefault("ode_form", "derivative")
        dialog = FormDialog(self, "Edit " + target[:-1], fields, initial)
        self.wait_window(dialog)
        if dialog.result is not None:
            if index is None:
                self.data[target].append(dialog.result)
            else:
                self.data[target][index] = dialog.result
            self.refresh()

    def add_variable(self):
        self._edit("variables", self._variable_fields())

    def edit_variable(self):
        selected = self.variables.selection()
        if selected:
            self._edit("variables", self._variable_fields(), int(selected[0]))

    @staticmethod
    def _variable_fields():
        return (("name", "Name", "h", None),
                ("kind", "Mode", "ode", ("instant", "ode")),
                ("ode_form", "ODE form (ignored for instant)", "relaxation",
                 ("relaxation", "derivative")),
                ("equation", "Instant value / target / full derivative",
                 "sigmoid((V + 50) / 5)", None),
                ("tau", "Time constant (ms; relaxation only)", "10", None),
                ("initial", "Initial value (ODE only)", "0.5", None))

    def remove_variable(self):
        selected = self.variables.selection()
        if selected:
            self.data["variables"].pop(int(selected[0]))
            self.refresh()

    def add_parameter(self):
        self._edit("parameters", self._parameter_fields())

    def edit_parameter(self):
        selected = self.parameters.selection()
        if selected:
            self._edit("parameters", self._parameter_fields(), int(selected[0]))

    def remove_parameter(self):
        selected = self.parameters.selection()
        if selected:
            self.data["parameters"].pop(int(selected[0]))
            self.refresh()

    @staticmethod
    def _parameter_fields():
        return (("name", "Name", "tauX", None),
                ("value", "Baseline value", "10", None),
                ("low", "Lower bound", "1", None),
                ("high", "Upper bound", "100", None),
                ("distribution", "Distribution", "log", ("log", "uniform", "neglog")),
                ("unit", "Unit (label)", "ms", None))

    def save(self):
        self.data.update(name=self.name_var.get().strip(), current=self.current_var.get().strip(),
                         na_rate=self.na_var.get().strip(), ca_rate=self.ca_var.get().strip())
        try:
            self.validate_module(self.data)
        except (ValueError, TypeError, KeyError) as exc:
            messagebox.showerror("Equation validation", str(exc), parent=self)
            return
        self.result = copy.deepcopy(self.data)
        self.destroy()
