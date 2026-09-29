"""Validated, declarative current modules for experimental single-cell models.

The expressions are a deliberately small mathematical language. They never
execute user Python statements, imports, attributes, indexing, or assignments.
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass


def _sigmoid(value):
    return 1.0 / (1.0 + math.exp(-max(-700.0, min(700.0, value))))


FUNCTIONS = {"exp": math.exp, "log": math.log, "log10": math.log10,
             "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
             "tanh": math.tanh, "abs": abs, "min": min, "max": max,
             "sigmoid": _sigmoid}
RESERVED = {"V", "Na", "Ca", "t", "I", "pi", *FUNCTIONS}
IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
OPERATORS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
COMPARISONS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


class EquationError(ValueError):
    pass


def _safe_pow(base, exponent):
    exponent = float(exponent)
    if not math.isfinite(exponent) or abs(exponent) > 64:
        raise EquationError("Exponent magnitude must be at most 64")
    return math.pow(base, exponent)


def _name(name, label, forbidden=()):
    if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
        raise EquationError(f"{label} must be an identifier (letters, numbers, underscores)")
    if name in RESERVED or name in forbidden:
        raise EquationError(f"{label} '{name}' is reserved or already used")
    return name


def compile_expression(expression, allowed, label):
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 500:
        raise EquationError(f"{label}: enter an expression (up to 500 characters)")
    try:
        tree = ast.parse(expression.replace("^", "**"), mode="eval")
    except SyntaxError as exc:
        raise EquationError(f"{label}: invalid expression near column {exc.offset}") from exc
    if len(list(ast.walk(tree))) > 100:
        raise EquationError(f"{label}: expression is too large")
    references = set()

    def check(node):
        if isinstance(node, ast.Expression):
            check(node.body)
        elif isinstance(node, ast.Constant):
            if type(node.value) not in (int, float) or abs(node.value) > 1e6:
                raise EquationError(f"{label}: only finite numeric literals up to 1e6 are allowed")
        elif isinstance(node, ast.Name):
            if node.id not in allowed:
                raise EquationError(f"{label}: unknown symbol '{node.id}'")
            references.add(node.id)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, OPERATORS):
            check(node.left)
            check(node.right)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            check(node.operand)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in FUNCTIONS or node.keywords or not node.args:
                raise EquationError(f"{label}: unsupported function")
            for argument in node.args:
                check(argument)
        elif isinstance(node, ast.Compare) and all(isinstance(op, COMPARISONS) for op in node.ops):
            check(node.left)
            for part in node.comparators:
                check(part)
        elif isinstance(node, ast.IfExp):
            check(node.test)
            check(node.body)
            check(node.orelse)
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            for part in node.values:
                check(part)
        else:
            raise EquationError(f"{label}: unsupported syntax ({type(node).__name__})")

    check(tree)
    class GuardPowers(ast.NodeTransformer):
        def visit_BinOp(self, node):
            self.generic_visit(node)
            if isinstance(node.op, ast.Pow):
                return ast.copy_location(ast.Call(func=ast.Name(id="_safe_pow", ctx=ast.Load()),
                                                  args=[node.left, node.right], keywords=[]), node)
            return node

    code = compile(ast.fix_missing_locations(GuardPowers().visit(tree)), "<equation>", "eval")

    def evaluate(context):
        try:
            value = float(eval(code, {"__builtins__": {}},
                               {**FUNCTIONS, "_safe_pow": _safe_pow,
                                "pi": math.pi, **context}))
        except (ArithmeticError, ValueError, TypeError, NameError, OverflowError) as exc:
            raise EquationError(f"{label}: {exc}") from exc
        if not math.isfinite(value):
            raise EquationError(f"{label}: nonfinite value")
        return value

    return evaluate, references


@dataclass
class CompiledModule:
    name: str
    parameters: dict
    states: list
    initial: list
    instant: list
    derivatives: list
    current: object
    na_rate: object
    ca_rate: object

    def evaluate(self, state, context):
        local = dict(context)
        local.update(zip(self.states, state))
        for name, equation in self.instant:
            local[name] = equation(local)
        current = self.current(local)
        local["I"] = current
        return (current, self.na_rate(local), self.ca_rate(local),
                [equation(local) for equation in self.derivatives])


def compile_modules(modules, base_parameters, fixed, state_names):
    """Validate definitions and return compiled modules; states use local names."""
    if modules is None:
        return []
    if not isinstance(modules, list) or len(modules) > 16:
        raise EquationError("Provide a list of at most 16 custom modules")
    compiled = []
    names = set()
    extra_parameters = set()
    available_ions = ({"Na"} if "Na_mM" in state_names else set()) | (
        {"Ca"} if "Ca_uM" in state_names else set())
    for item in modules:
        if not isinstance(item, dict):
            raise EquationError("Each module must be an object")
        name = _name(item.get("name"), "Module name", names)
        names.add(name)
        parameters = {}
        for param in item.get("parameters", []):
            key = _name(param.get("name"), "Parameter", set(base_parameters) | set(fixed) |
                        extra_parameters | set(parameters))
            value, low, high = (float(param[field]) for field in ("value", "low", "high"))
            distribution = param.get("distribution", "uniform")
            if not all(map(math.isfinite, (value, low, high))) or not low < high or (
                    distribution not in ("uniform", "log", "neglog")) or (
                    distribution == "log" and low <= 0) or (
                    distribution == "neglog" and high >= 0):
                raise EquationError(f"Parameter '{key}': invalid value or search bounds")
            parameters[key] = value
            extra_parameters.add(key)
        variables = item.get("variables", [])
        if not isinstance(variables, list) or len(variables) > 32:
            raise EquationError(f"{name}: provide at most 32 variables")
        variables_by_name = {}
        for variable in variables:
            key = _name(variable.get("name"), "Variable", set(base_parameters) |
                        set(fixed) | set(parameters) | set(variables_by_name))
            if variable.get("kind") not in ("instant", "ode"):
                raise EquationError(f"{key}: choose instant or ode")
            variables_by_name[key] = variable
        allowed = ({"V", "t", "pi"} | available_ions | set(base_parameters) |
                   set(fixed) | set(parameters) | set(variables_by_name))
        instant = {}
        derivatives = {}
        state_keys = []
        initial = []
        for key, variable in variables_by_name.items():
            equation, references = compile_expression(variable.get("equation"), allowed,
                                                       f"{name}.{key}")
            if variable["kind"] == "instant":
                instant[key] = (equation, references)
            else:
                initial_value = float(variable.get("initial", 0))
                if not math.isfinite(initial_value):
                    raise EquationError(f"{name}.{key}: initial value must be finite")
                state_keys.append(key)
                initial.append(initial_value)
                # Absent ode_form is the original full-derivative JSON format.
                # Its meaning is retained when loading previously saved modules.
                ode_form = variable.get("ode_form", "derivative")
                if ode_form == "relaxation":
                    tau, _ = compile_expression(variable.get("tau"), allowed,
                                                f"{name}.{key} time constant")

                    def relaxation(context, target=equation, time_constant=tau,
                                   state_name=key, label=f"{name}.{key}"):
                        tau_ms = time_constant(context)
                        if tau_ms <= 0:
                            raise EquationError(f"{label}: time constant must be positive (ms)")
                        return (target(context) - context[state_name]) / tau_ms

                    derivatives[key] = relaxation
                elif ode_form == "derivative":
                    derivatives[key] = equation
                else:
                    raise EquationError(f"{name}.{key}: choose relaxation or derivative")
        ordered = []
        pending = dict(instant)
        while pending:
            ready = [key for key, (_, refs) in pending.items()
                     if not refs.intersection(pending)]
            if not ready:
                raise EquationError(f"{name}: instantaneous variable dependency cycle")
            for key in ready:
                ordered.append((key, pending.pop(key)[0]))
        current, _ = compile_expression(item.get("current"), allowed, f"{name} current")
        rate_allowed = allowed | {"I"}
        na_rate, na_refs = compile_expression(item.get("na_rate", "0"), rate_allowed,
                                               f"{name} Na rate")
        ca_rate, ca_refs = compile_expression(item.get("ca_rate", "0"), rate_allowed,
                                               f"{name} Ca rate")
        if "Na" not in available_ions and (item.get("na_rate", "0").strip() != "0" or
                                           "Na" in na_refs):
            raise EquationError(f"{name}: base model has no Na state")
        if "Ca" not in available_ions and (item.get("ca_rate", "0").strip() != "0" or
                                           "Ca" in ca_refs):
            raise EquationError(f"{name}: base model has no Ca state")
        compiled.append(CompiledModule(name, parameters, state_keys, initial, ordered,
                         [derivatives[key] for key in state_keys], current, na_rate, ca_rate))
    return compiled


def module_parameters(modules):
    return {param["name"]: float(param["value"])
            for module in (modules or []) for param in module.get("parameters", [])}


def module_ranges(modules):
    return {param["name"]: (float(param["low"]), float(param["high"]),
                            param.get("distribution", "uniform"), param.get("unit", ""))
            for module in (modules or []) for param in module.get("parameters", [])}
