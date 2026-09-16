"""Strict, versioned JSON configuration with small, safe numeric expressions."""
from __future__ import annotations

import ast
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re


class ConfigurationError(ValueError):
    """A user-editable configuration is inconsistent or unsupported."""


def numeric(value: object, parameters: dict[str, object] | None = None) -> complex:
    """Accept finite numbers or arithmetic using pi, i, and parameter names.

    No Python evaluation, calls, attributes, indexing, or file access. Parameter
    definitions contain constants; references are used in the optical flow.
    """
    parameters = parameters or {}
    if isinstance(value, bool):
        raise ConfigurationError('Boolean values are not optical numbers')
    if isinstance(value, (int, float, complex)):
        result = complex(value)
    elif isinstance(value, str):
        if len(value) > 200:
            raise ConfigurationError('Numeric expressions must be at most 200 characters')
        try:
            tree = ast.parse(value, mode='eval')
            if len(list(ast.walk(tree))) > 60:
                raise ConfigurationError('Numeric expression is too complex')
            def visit(node: ast.AST) -> complex:
                if isinstance(node, ast.Constant) and type(node.value) in (int, float, complex):
                    return complex(node.value)
                if isinstance(node, ast.Name):
                    if node.id in ('pi', 'i'):
                        return {'pi': math.pi, 'i': 1j}[node.id]
                    if node.id in parameters:
                        return numeric(parameters[node.id])
                    raise ConfigurationError(f'Unknown parameter: {node.id}')
                if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                    return visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
                if isinstance(node, ast.BinOp):
                    a, b = visit(node.left), visit(node.right)
                    if isinstance(node.op, ast.Add):
                        return a + b
                    if isinstance(node.op, ast.Sub):
                        return a - b
                    if isinstance(node.op, ast.Mult):
                        return a * b
                    if isinstance(node.op, ast.Div):
                        return a / b
                raise ConfigurationError('Use numbers, names, pi, i, +, -, *, / and parentheses only')
            result = visit(tree.body)
        except (SyntaxError, ZeroDivisionError, OverflowError) as exc:
            raise ConfigurationError(f'Invalid numeric expression: {value!r}') from exc
    else:
        raise ConfigurationError(f'Expected a number or arithmetic expression, got {value!r}')
    if not (math.isfinite(result.real) and math.isfinite(result.imag)):
        raise ConfigurationError('Optical numbers must be finite')
    return result


def real(value: object, parameters: dict[str, object] | None = None) -> float:
    result = numeric(value, parameters)
    if result.imag != 0:
        raise ConfigurationError(f'Expected a real number, got {value!r}')
    return result.real


def keys(value: dict, allowed: set[str], required: set[str], context: str) -> None:
    if not isinstance(value, dict):
        raise ConfigurationError(f'{context} must be an object')
    if set(value) - allowed:
        raise ConfigurationError(f'{context}: unknown keys {sorted(set(value) - allowed)}')
    if required - set(value):
        raise ConfigurationError(f'{context}: missing keys {sorted(required - set(value))}')


def identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', value):
        raise ConfigurationError(f'Use a letter followed by letters, digits or underscores: {value!r}')
    return value


def read_json(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ConfigurationError(f'{path}: duplicate key {key!r}')
            result[key] = value
        return result
    try:
        value = json.loads(path.read_text(), object_pairs_hook=unique)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f'{path}:{exc.lineno}: {exc.msg}') from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f'{path}: expected a JSON object')
    return value


@dataclass(frozen=True)
class Configuration:
    flow: dict
    parameters: dict
    phase_values: dict[str, float]
    scan: dict
    flow_path: Path | None = None
    parameter_path: Path | None = None


def from_dicts(flow: dict, parameters: dict) -> Configuration:
    keys(flow, {'schema_version', 'name', 'description', 'phases', 'input', 'elements', 'outputs'},
         {'schema_version', 'name', 'phases', 'input', 'elements', 'outputs'}, 'flow')
    keys(parameters, {'schema_version', 'name', 'description', 'values', 'phase_values', 'scan', 'provenance', 'normalization'},
         {'schema_version', 'values', 'phase_values'}, 'parameters')
    if type(flow['schema_version']) is not int or flow['schema_version'] != 1:
        raise ConfigurationError('Optical flows use schema_version 1')
    if type(parameters['schema_version']) is not int or parameters['schema_version'] not in (1, 2):
        raise ConfigurationError('Parameter schema_version must be 1 (exploratory) or 2 (provenance required)')
    for source in (flow, parameters):
        for key in ('name', 'description'):
            if key in source and not isinstance(source[key], str):
                raise ConfigurationError(f'{key} must be text')
    phases = flow['phases']
    if not isinstance(phases, list) or len(phases) > 4:
        raise ConfigurationError('phases must be a list of at most four phase names')
    for phase in phases:
        identifier(phase)
    if len(set(phases)) != len(phases):
        raise ConfigurationError('Phase names must be unique')
    values = parameters['values']
    if not isinstance(values, dict):
        raise ConfigurationError('values must be an object')
    for name, value in values.items():
        identifier(name)
        if name in ('pi', 'i') or name in phases:
            raise ConfigurationError(f'Reserved parameter name: {name}')
        numeric(value)
    if not isinstance(parameters['phase_values'], dict) or set(parameters['phase_values']) != set(phases):
        raise ConfigurationError('phase_values must specify exactly the phase names in the flow')
    phase_values = {name: real(value) for name, value in parameters['phase_values'].items()}
    scan = parameters.get('scan', {})
    keys(scan, {'points', 'start_rad', 'stop_rad', 'dark_threshold'}, set(), 'scan')
    points = scan.get('points', 81)
    if type(points) is not int or not 9 <= points <= 501:
        raise ConfigurationError('scan.points must be an integer from 9 to 501')
    scan = dict(points=points, start_rad=real(scan.get('start_rad', 0)),
                stop_rad=real(scan.get('stop_rad', '2*pi')),
                dark_threshold=real(scan.get('dark_threshold', 1e-12)))
    if scan['stop_rad'] <= scan['start_rad'] or scan['dark_threshold'] < 0:
        raise ConfigurationError('Scan stop must exceed start; dark_threshold must be nonnegative')
    if parameters['schema_version'] == 2:
        from .provenance import validate_provenance
        validate_provenance(parameters)
    return Configuration(flow, parameters, phase_values, scan)


def load_configuration(flow_path: Path, parameter_path: Path) -> Configuration:
    config = from_dicts(read_json(flow_path), read_json(parameter_path))
    return Configuration(config.flow, config.parameters, config.phase_values, config.scan,
                         flow_path.resolve(), parameter_path.resolve())
