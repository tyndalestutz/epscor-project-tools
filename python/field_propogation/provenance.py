"""Parameter provenance for measurement-first forward predictions.

Version-1 examples remain reproducible but are explicitly unverified. Version 2
requires every parameter and its uncertainty/physical plane to be classified.
"""
from __future__ import annotations

import numpy as np

from .configuration import ConfigurationError, numeric

STATUSES = {'measured', 'derived', 'convention', 'assumed', 'nuisance', 'fitted_nuisance'}
REQUIRED = {'status', 'units', 'quantity', 'plane', 'sources', 'uncertainty', 'notes'}


def validate_provenance(parameters: dict) -> None:
    metadata = parameters.get('provenance')
    if not isinstance(metadata, dict) or set(metadata) != set(parameters['values']):
        raise ConfigurationError('Version 2 requires provenance for every value, with no extra entries')
    normalization = parameters.get('normalization', {})
    if normalization.get('plane') != 'P0_before_PBS' or normalization.get('field_units') != 'sqrt(W)':
        raise ConfigurationError('Measured hybrid model uses P0_before_PBS input amplitudes in sqrt(W)')
    for name, item in metadata.items():
        if not isinstance(item, dict) or not REQUIRED <= item.keys():
            raise ConfigurationError(f'{name}: provenance needs {sorted(REQUIRED)}')
        if item['status'] not in STATUSES:
            raise ConfigurationError(f'{name}: unknown provenance status')
        if any(not isinstance(item[key], str) or not item[key].strip()
               for key in ('units', 'quantity', 'plane', 'notes')):
            raise ConfigurationError(f'{name}: units, quantity, plane and notes must be explicit')
        if not isinstance(item['sources'], list) or any(not isinstance(s, str) for s in item['sources']):
            raise ConfigurationError(f'{name}: sources must be measurement IDs')
        if item['status'] in {'measured', 'derived'} and not item['sources']:
            raise ConfigurationError(f'{name}: measured/derived values need source measurements')
        uncertainty = item['uncertainty']
        if uncertainty is not None and (isinstance(uncertainty, bool) or not isinstance(uncertainty, (float, int))
                                        or not np.isfinite(uncertainty) or uncertainty < 0):
            raise ConfigurationError(f'{name}: uncertainty must be a nonnegative standard uncertainty or null')
        if item['status'] in {'measured', 'derived'} and uncertainty is None:
            raise ConfigurationError(f'{name}: measured/derived value needs a standard uncertainty')
        if item['status'] in {'nuisance', 'fitted_nuisance'}:
            bounds = item.get('bounds')
            value = numeric(parameters['values'][name])
            if not isinstance(bounds, list) or len(bounds) != 2 or value.imag:
                raise ConfigurationError(f'{name}: nuisance parameters require real lower/upper bounds')
            if not bounds[0] <= value.real <= bounds[1] or not np.isfinite(bounds).all():
                raise ConfigurationError(f'{name}: selected nuisance value lies outside finite bounds')
        if item['status'] == 'fitted_nuisance' and not item.get('identifiability_report'):
            raise ConfigurationError(f'{name}: a fitted nuisance needs an identifiability report')


def interpretation(parameters: dict) -> str:
    if parameters.get('schema_version') != 2:
        return 'EXPLORATORY / HISTORICAL: no enforced independent-measurement provenance'
    statuses = {p['status'] for p in parameters['provenance'].values()}
    if statuses & {'assumed', 'nuisance', 'fitted_nuisance'}:
        return 'CONDITIONAL FORWARD MODEL: unresolved assumptions or nuisance parameters remain'
    return 'MEASUREMENT-DERIVED FORWARD MODEL: provenance supplied; predictive validity is untested'
