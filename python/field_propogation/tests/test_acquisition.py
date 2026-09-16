"""Synthetic instrument rehearsal. No fixture is an experimental measurement."""
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from field_propogation.acquisition.plan import make_plan
from field_propogation.acquisition.session import initialize, register, readiness, write_json, read_section, write_section
from field_propogation.acquisition.reduction import power_fraction, field_transmission, reconstruct_jones
from field_propogation.acquisition.reduction import state_matrix
from field_propogation.acquisition.build import build_parameters
from field_propogation.acquisition.prediction import freeze, predict, compare, verify_bundle
from field_propogation.configuration import ConfigurationError, from_dicts
from field_propogation.detection import coherency, observables
from field_propogation.observables import STOKES_MATRICES
from field_propogation.propagation import compile_network
from field_propogation.provenance import validate_provenance


def synthetic_campaign(path):
    """Known passive optics, exact synthetic readings with declared instrument error."""
    initialize(path)
    context = read_section(path, 'session')
    context.update(operator='SYNTHETIC TEST ONLY', bench_id='NO HARDWARE', wavelength_nm=1550,
                   instrument_calibrations={'synthetic': 'exact simulator; test fixture'},
                   confirmed_planes=True, basis_verified=True)
    write_section(path, 'session', context)
    choices = read_section(path, 'choices')
    for name, choice in choices.items():
        reflected = ('NPBS1_AD' in name or 'NPBS1_BC' in name or
                     'NPBS2_CF' in name or 'NPBS2_DE' in name)
        choice.update(value=float(np.pi/2 if reflected else 0), reason='SYNTHETIC conditional phase choice; not a bench measurement')
    write_section(path, 'choices', choices)
    states = {'H': np.array([1, 0]), 'V': np.array([0, 1]),
              'D': np.array([1, 1])/np.sqrt(2), 'R': np.array([1, 1j])/np.sqrt(2)}
    angle = .31
    rot = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    matrix = rot @ np.diag([.83*np.exp(.2j), .91*np.exp(-.4j)]) @ rot.T
    for step in make_plan()['steps']:
        if not step['required']:
            continue
        for repeat in range(3):
            if step['kind'] == 'input_state':
                field = states['D']
                values = dict(power=1000., dark=0.)
            else:
                if step['kind'] == 'jones_response':
                    field = matrix @ states[step['input_state']]
                    fraction = float(np.vdot(field, field).real)
                elif step['id'].startswith('PBS'):
                    main = (step['id'] in ('PBS_A_H', 'PBS_B_V'))
                    fraction = .99**2 * (1-.01**2 if main else .01**2)
                else:
                    fraction = .98**2 / 2
                values = dict(power_in_before=1000., power_in_after=1000.,
                              power_out=1000*fraction, dark_in=0., dark_out=0.)
            if step['kind'] in ('input_state', 'jones_response'):
                raw = np.einsum('a,jab,b->j', field.conj(), STOKES_MATRICES, field).real
                values.update(zip(('s1', 's2', 's3'), raw[1:]/raw[0]))
                values['dop'] = 1.
            reading = dict(values=values, uncertainties={key: 1e-5 for key in values},
                           power_unit='uW', detectors={'all': 'synthetic'},
                           utc=datetime.now(timezone.utc).isoformat(),
                           notes=f'SYNTHETIC ONLY {step["id"]}, repeat {repeat}')
            source = path/'synthetic_reading.json'
            write_json(source, reading)
            register(path, step['id'], source)
    return matrix


class MeasurementTests(unittest.TestCase):
    def test_power_is_square_rooted_and_background_drift_checked(self):
        reading = dict(power_unit='uW', values=dict(power_in_before=881.,power_in_after=881.,power_out=782.,dark_in=1.,dark_out=2.),
                       uncertainties={key: .1 for key in ('power_in_before','power_in_after','power_out','dark_in','dark_out')})
        fraction, sigma = power_fraction(reading)
        amplitude, error = field_transmission(fraction, sigma)
        self.assertAlmostEqual(amplitude, np.sqrt(780/880))
        self.assertGreater(error, 0)
        reading['values']['power_in_after'] = 1000
        with self.assertRaises(ConfigurationError): power_fraction(reading)
        with self.assertRaises(ConfigurationError): field_transmission(0, .01)

    def test_jones_tomography_predicts_unused_input(self):
        matrix = np.array([[.6, .1j], [.2j, .7*np.exp(.3j)]])
        states = {}
        for name, state in {'H':[1,0], 'V':[0,1], 'D':np.array([1,1])/np.sqrt(2), 'R':np.array([1,1j])/np.sqrt(2)}.items():
            field = matrix @ state
            states[name] = np.outer(field, field.conj())
        reconstructed, _ = reconstruct_jones(states, 1e-8)
        reconstructed *= np.exp(-1j*np.angle(np.vdot(matrix, reconstructed)))
        np.testing.assert_allclose(reconstructed, matrix, atol=1e-12)
        states['R'] = states['D']
        with self.assertRaises(ConfigurationError): reconstruct_jones(states, .01)

    def test_spatial_coherency_and_passive_fiber_projection(self):
        fields = np.array([[1, 0], [0, 1]], complex)
        result = observables(coherency(fields, np.eye(2)))
        self.assertAlmostEqual(result['intensity'], 2.)
        self.assertAlmostEqual(result['dop'], 0.)
        h = np.array([.4, .3j])
        coupled = coherency(fields, np.eye(2), h)
        expected = h @ fields
        np.testing.assert_allclose(coupled, np.outer(expected, expected.conj()))
        with self.assertRaises(ConfigurationError): coherency(fields, np.eye(2), np.array([1,1]))
        with self.assertRaises(ConfigurationError): coherency(fields, [[1,2],[2,1]])
        # Orthogonal polarizations cannot acquire an intensity fringe from overlap alone.
        for phase in np.linspace(0, 2*np.pi, 11):
            shifted = fields * np.array([1, np.exp(1j*phase)])[:, None]
            self.assertAlmostEqual(observables(coherency(shifted, np.ones((2,2))))['intensity'], 2.)

    def test_blank_campaign_cannot_build(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'campaign'; initialize(path)
            self.assertFalse(readiness(path)['complete'])
            with self.assertRaises(ConfigurationError): build_parameters(path)

    def test_compact_campaign_supports_direct_editing_without_import_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            initialize(path)
            self.assertEqual({p.name for p in path.iterdir()},
                             {'campaign.json','validation_schedule.csv','README.md'})
            campaign=json.loads((path/'campaign.json').read_text())
            reading=campaign['reading_templates']['power_ratio']
            reading.update(values=dict(power_in_before=100.,power_in_after=100.,power_out=50.,dark_in=0.,dark_out=0.),
                           utc=datetime.now(timezone.utc).isoformat(),detectors={'power':'meter'},notes='SYNTHETIC direct-edit test')
            reading['uncertainties']={key:.1 for key in reading['values']}
            campaign['measurements']['PBS_A_H']=[reading]
            write_json(path/'campaign.json',campaign)
            status=readiness(path)
            self.assertEqual(next(s for s in status['required_measurements_missing'] if s['step']=='PBS_A_H')['repeats'],1)
            self.assertEqual(len(list(path.iterdir())),3)
            campaign['measurements']['PBS_A_H'].append(reading)
            write_json(path/'campaign.json',campaign)
            with self.assertRaises(ConfigurationError): readiness(path)

    def test_polarization_dependent_scattering_and_combined_passivity(self):
        # Explicit H/V-dependent mixing, with polarization-dependent phase.
        t=np.diag([np.cos(.3), np.cos(.7)])
        r=1j*np.diag([np.sin(.3), np.sin(.7)])
        matrix=np.block([[t,r],[r,t]])
        def strings(array):
            return [[f'{v.real}+({v.imag})*i' for v in row] for row in array]
        flow=dict(schema_version=1,name='test',phases=[],input=dict(name='in',jones=[1,1]),
                  elements=[dict(name='source_split',type='splitter',inputs=['in'],outputs=['a','b'],matrix=[[1,0],[0,1],[0,0],[0,0]]),
                            dict(name='measured',type='scattering',inputs=['a','b'],outputs=['E','F'],matrix=strings(matrix))],outputs=['E','F'])
        params=dict(schema_version=1,name='test',values={},phase_values={})
        network=compile_network(from_dicts(flow,params))
        fields=network.evaluate()
        np.testing.assert_allclose(fields['E'],[np.cos(.3),np.cos(.7)])
        np.testing.assert_allclose(fields['F'],1j*np.array([np.sin(.3),np.sin(.7)]))
        self.assertAlmostEqual(sum(np.vdot(f,f).real for f in fields.values()),2)
        flow['elements'][1]['matrix']=[[.7]*4 for _ in range(4)]
        with self.assertRaises(ConfigurationError): compile_network(from_dicts(flow,params))

    def test_low_dop_cannot_be_normalized_into_a_jones_measurement(self):
        reading=dict(power_unit='uW',values=dict(power=1000,dark=0,s1=1,s2=0,s3=0,dop=.5),uncertainties={})
        reading['uncertainties']={key:0.001 for key in reading['values']}
        with self.assertRaises(ConfigurationError):
            state_matrix(reading,make_plan()['quality'],transmission=False)

    def test_synthetic_end_to_end_and_tamper_guards(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); session=root/'session'
            synthetic_campaign(session)
            self.assertTrue(readiness(session)['complete'])
            flow, params, diagnostics=build_parameters(session)
            self.assertEqual(set(diagnostics), set('ABCD'))
            self.assertAlmostEqual(params['values']['amplitude_NPBS1_AC_H'], .98/np.sqrt(2))
            compile_network(from_dicts(flow,params))
            broken=json.loads(json.dumps(params)); del broken['provenance']['input_x']
            with self.assertRaises(ConfigurationError): validate_provenance(broken)
            with self.assertRaises(ConfigurationError): register(session, 'arm_D_R', session/'synthetic_reading.json')
            freeze(session, root/'frozen'); verify_bundle(root/'frozen')
            predict(root/'frozen', root/'prediction')
            with (root/'prediction/predictions.csv').open() as handle: predicted=list(csv.DictReader(handle))
            self.assertEqual(len(predicted), 336)
            readings=root/'validation.csv'
            columns=['sample_id','port','utc','power_w','power_sigma_w','s1','s2','s3','stokes_sigma','dop','detector_id','notes']
            with readings.open('w',newline='') as handle:
                writer=csv.DictWriter(handle,fieldnames=columns);writer.writeheader()
                for row in predicted:
                    record={k:row[k] for k in ('sample_id','port','power_w','s1','s2','s3','dop')}
                    record.update(utc=datetime.now(timezone.utc).isoformat(),power_sigma_w=1e-8,stokes_sigma=.001,detector_id='synthetic',notes='SYNTHETIC validation fixture')
                    writer.writerow(record)
            result=compare(root/'frozen',root/'prediction',readings,root/'comparison')
            self.assertTrue(result['complete'])
            self.assertTrue(all(r['power_error_w']==0 for r in result['residuals']))
            with readings.open() as handle:
                missing_pol=list(csv.DictReader(handle))
            for key in ('s1','s2','s3','stokes_sigma','dop'):
                missing_pol[0][key]=''
            missing_pol[0]['notes']='SYNTHETIC invalid polarimeter reading; power still available'
            with readings.open('w',newline='') as handle:
                writer=csv.DictWriter(handle,fieldnames=columns);writer.writeheader();writer.writerows(missing_pol)
            partial=compare(root/'frozen',root/'prediction',readings,root/'partial-polarization')
            self.assertTrue(partial['complete'])
            self.assertEqual(partial['polarization_comparisons'],335)
            self.assertIsNone(partial['residuals'][0]['stokes_error_norm'])
            text=readings.read_text().replace(datetime.now(timezone.utc).date().isoformat(), '2000-01-01')
            readings.write_text(text)
            with self.assertRaises(ConfigurationError): compare(root/'frozen',root/'prediction',readings,root/'old-comparison')
            (root/'frozen/choices.json').write_text('{}')
            with self.assertRaises(ConfigurationError): verify_bundle(root/'frozen')

if __name__ == '__main__': unittest.main()
