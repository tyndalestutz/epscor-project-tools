"""Physics, configuration, reporting, and archived-result regression checks.

Run: PYTHONPATH=python python -m unittest discover -s python/field_propogation/tests -v
"""
from __future__ import annotations

import csv
from pathlib import Path
import shutil
import tempfile
import unittest

import numpy as np

from field_propogation.analysis import evaluate_with_sensitivity
from field_propogation.configuration import ConfigurationError, from_dicts, load_configuration, numeric, read_json
from field_propogation.elements import Retarder
from field_propogation.observables import describe, normalized_stokes
from field_propogation.physical_hybrid_mzi import PhysicalParameters, propagate
from field_propogation.propagation import compile_network
from field_propogation.run_simulation import run, serializable
from field_propogation.symbolic_intensity import input_transfer_modes, intensity_form_modes
from field_propogation.archive.effective_fits.parameter_study import contribution_study

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE.parents[1]
CONFIGS = BASE/'configs'
EXPERIMENTS = ROOT/'experiments/polarization_locking/2026-08-13'
FIT_DIR = EXPERIMENTS/'192530_field-model-calibration_phi2-power-model-0'
MAP_DIR = EXPERIMENTS/'200934_phi1-fringe-map_phi1-fringe-map-1'


def network(parameters='ideal', flow='hybrid_mzi'):
    flow_base = BASE/'archive/effective_fits/configs' if flow == 'hybrid_mzi_relative_fit' else CONFIGS
    param_base = BASE/'archive/effective_fits/configs' if parameters == 'historical_fit' else CONFIGS
    return compile_network(load_configuration(flow_base/f'flows/{flow}.json', param_base/f'parameters/{parameters}.json'))


class PhysicsTests(unittest.TestCase):
    def test_ideal_fields_stokes_and_power_from_existing_reference(self):
        model = network()
        p1 = np.linspace(-2, 5, 13)[:, None]
        p2 = np.linspace(-1, 7, 19)[None, :]
        fields = model.evaluate({'phi1':p1, 'phi2':p2})
        expected = np.stack(np.broadcast_arrays(np.exp(1j*p1)*(np.exp(1j*p2)-1)/2,
                            1j*(np.exp(1j*p2)+1)/2),axis=-1)
        np.testing.assert_allclose(fields['E'],expected,atol=1e-14)
        stokes = np.stack(np.broadcast_arrays(np.cos(p2), np.sin(p2)*np.cos(p1), np.sin(p2)*np.sin(p1)),axis=-1)
        np.testing.assert_allclose(describe(fields['E'])['stokes'],stokes,atol=1e-14)
        for field in fields.values():
            np.testing.assert_allclose(describe(field)['intensity'],1,atol=1e-14)

    def test_unequal_input_matches_reported_scalar_amplitude_law(self):
        model = network('unequal_input')
        phi = np.linspace(0,2*np.pi,101)
        fields = model.evaluate({'phi2':phi})
        np.testing.assert_allclose(describe(fields['E'])['intensity'],np.sin(phi/2)**2+.49*np.cos(phi/2)**2,atol=1e-14)
        np.testing.assert_allclose(describe(fields['F'])['intensity'],np.cos(phi/2)**2+.49*np.sin(phi/2)**2,atol=1e-14)

    def test_harmonics_reconstruct_stokes_and_derivatives(self):
        model=network('historical_fit','hybrid_mzi_relative_fit')
        phase={'phi1':.84,'phi2':2.1}
        for port,field in model.evaluate(phase).items():
            result=sum(c*np.exp(1j*sum(k*phase[p] for k,p in zip(mode,model.phases)))
                       for mode,c in model.stokes_harmonics(port).items())
            np.testing.assert_allclose(result,describe(field)['raw_stokes'],atol=1e-13)
        for p in model.phases:
            actual=evaluate_with_sensitivity(model,phase,p)
            step=1e-6
            plus=model.evaluate(dict(phase,**{p:phase[p]+step}))
            minus=model.evaluate(dict(phase,**{p:phase[p]-step}))
            for port in model.outputs:
                hi,lo=describe(plus[port]),describe(minus[port])
                np.testing.assert_allclose(actual[port]['intensity_derivative'],(hi['intensity']-lo['intensity'])/(2*step),atol=2e-9)
                np.testing.assert_allclose(actual[port]['stokes_derivative'],(hi['stokes']-lo['stokes'])/(2*step),atol=2e-9)

    def test_arbitrary_flow_and_measured_axis_response(self):
        model=network('single_element','single_element')
        field=model.evaluate()['out']
        np.testing.assert_allclose(field,[.9,.7*np.exp(1j*np.pi/3)],atol=1e-14)
        obs=describe(field)
        self.assertAlmostEqual(float(obs['intensity']),1.3)
        self.assertAlmostEqual(float(np.linalg.norm(obs['stokes'])),1)
        self.assertLess(float(obs['stokes'][2]),0)

    def test_custom_matrix_and_dark_output(self):
        flow = read_json(CONFIGS / 'flows/single_element.json')
        flow['elements'][0] = {
            'name': 'polarizer', 'type': 'matrix', 'inputs': ['Ein'],
            'outputs': ['out'], 'matrix': [[0, 0], [0, 1]],
        }
        params = dict(schema_version=1, values={'ax': 1, 'ay': 0}, phase_values={})
        model = compile_network(from_dicts(flow, params))
        np.testing.assert_array_equal(model.evaluate()['out'], [0, 0])
        self.assertFalse(bool(describe(model.evaluate()['out'])['defined']))
        flow['elements'][0]['matrix'] = [[1, 1], [0, 1]]
        with self.assertRaisesRegex(ConfigurationError, 'gain exceeds'):
            compile_network(from_dicts(flow, params))

    def test_repeated_control_produces_correct_higher_field_harmonic(self):
        flow = dict(
            schema_version=1, name='Two phase plates', phases=['phi1'],
            input={'name': 'Ein', 'jones': [1, 0]},
            elements=[
                dict(name='first', type='phase', inputs=['Ein'], outputs=['middle'], phase='phi1'),
                dict(name='second', type='phase', inputs=['middle'], outputs=['out'], phase='phi1'),
            ], outputs=['out'],
        )
        params = dict(schema_version=1, values={}, phase_values={'phi1': 0.3})
        model = compile_network(from_dicts(flow, params))
        self.assertEqual(set(model.fields['out']), {(2,)})
        np.testing.assert_allclose(model.evaluate()['out'], [np.exp(0.6j), 0])
        np.testing.assert_allclose(describe(model.evaluate()['out'])['intensity'], 1)

    def test_dark_and_singular_polarization_is_not_invented(self):
        dark=describe(np.zeros(2,complex))
        self.assertFalse(bool(dark['defined']))
        self.assertTrue(np.isnan(dark['stokes']).all())
        self.assertIsNone(serializable(dark)['theta_rad'])
        with self.assertRaises(ValueError):
            normalized_stokes(np.zeros(2))
        circular=describe(np.array([1,1j])/np.sqrt(2))
        self.assertTrue(np.isnan(circular['theta_rad']))
        self.assertAlmostEqual(float(circular['eta_rad']),-np.pi/4)
        linear_y=describe(np.array([0,1]))
        self.assertTrue(np.isnan(linear_y['u_rad']))
        self.assertAlmostEqual(float(linear_y['theta_rad']),0)

    def test_lossless_network_conserves_total_power(self):
        flow=read_json(CONFIGS/'flows/hybrid_mzi.json')
        params=read_json(CONFIGS/'parameters/ideal.json')
        params['values'].update(pbs_leakage_rad=.23,npbs1_mixing_rad=.61,npbs2_mixing_rad=.92,
                                ax='0.8+0.3*i',ay=.6,axis_a=.31,retardance_a=.7,axis_d=-.22,retardance_d=-1.1)
        fields=compile_network(from_dicts(flow,params)).evaluate({'phi1':np.linspace(0,6,31),'phi2':1.31})
        total=sum(describe(f)['intensity'] for f in fields.values())
        np.testing.assert_allclose(total,.8**2+.3**2+.6**2,atol=1e-14)

    def test_backward_compatibility_delta_leakage_and_retarders(self):
        # Independent direct matrix chain, including input delta before leakage.
        p=PhysicalParameters(ax=.9,ay=.6,delta_rad=.42,pbs_leakage_rad=.15,npbs1_mixing_rad=.62,npbs2_mixing_rad=.81,
                             loss_a=.8,loss_c=.7,retarder_a=Retarder(.2,.4),retarder_d=Retarder(-.3,.7))
        phi1,phi2=.71,1.2
        ein=np.array([p.ax*np.exp(1j*p.delta_rad),p.ay])
        a=np.diag([np.cos(p.pbs_leakage_rad),1j*np.sin(p.pbs_leakage_rad)])@ein
        b=np.diag([1j*np.sin(p.pbs_leakage_rad),np.cos(p.pbs_leakage_rad)])@ein
        a=p.loss_a*p.retarder_a.matrix()@a*np.exp(1j*phi1)
        b=p.loss_b*p.retarder_b.matrix()@b
        c=np.cos(p.npbs1_mixing_rad)*a+1j*np.sin(p.npbs1_mixing_rad)*b
        d=1j*np.sin(p.npbs1_mixing_rad)*a+np.cos(p.npbs1_mixing_rad)*b
        c=p.loss_c*p.retarder_c.matrix()@c*np.exp(1j*phi2)
        d=p.loss_d*p.retarder_d.matrix()@d
        expected_e=np.cos(p.npbs2_mixing_rad)*c+1j*np.sin(p.npbs2_mixing_rad)*d
        expected_f=1j*np.sin(p.npbs2_mixing_rad)*c+np.cos(p.npbs2_mixing_rad)*d
        actual=propagate(phi1,phi2,p)
        self.assertEqual(set(actual),{'Ein','A_before_phi1','A_after_phi1','B','C','C_after_phi2','D','E','F'})
        np.testing.assert_allclose(actual['E'],expected_e,atol=1e-14)
        np.testing.assert_allclose(actual['F'],expected_f,atol=1e-14)


class SymbolicAmplitudeTests(unittest.TestCase):
    def test_independent_amplitudes_and_input_phase_reconstruct_intensity(self):
        model = network('historical_fit', 'hybrid_mzi_relative_fit')
        transfers = input_transfer_modes(model)
        phases = {'phi1': 1.13, 'phi2': -0.42}
        for ax, ay, delta in [(0.3, 1.7, 0.83), (1.4, 0.2, -1.1), (0, 0.8, 2.0), (0.7, 0, 0)]:
            import copy
            flow = copy.deepcopy(model.config.flow)
            flow['input']['jones'] = [ax * np.exp(1j * delta), ay]
            direct = compile_network(from_dicts(flow, model.config.parameters)).evaluate(phases)
            for port, matrices in transfers.items():
                def phase_factor(mode):
                    return np.exp(1j * sum(k * phases[p] for k, p in zip(mode, model.phases)))
                transfer = sum(matrix * phase_factor(mode) for mode, matrix in matrices.items())
                np.testing.assert_allclose(transfer @ flow['input']['jones'], direct[port], atol=1e-14)
                forms = sum(c * phase_factor(mode) for mode, c in intensity_form_modes(matrices).items())
                np.testing.assert_allclose(forms.imag, 0, atol=1e-14)
                A, B, real_g, imag_g = forms.real
                prediction = ax**2 * A + ay**2 * B + 2 * ax * ay * (real_g * np.cos(delta) + imag_g * np.sin(delta))
                self.assertAlmostEqual(prediction, float(describe(direct[port])['intensity']), places=13)

    def test_symbolic_intensity_survives_blocked_configured_input(self):
        flow = read_json(CONFIGS / 'flows/hybrid_mzi.json')
        params = read_json(CONFIGS / 'parameters/ideal.json')
        params['values']['ay'] = 0
        transfer = input_transfer_modes(compile_network(from_dicts(flow, params)))
        self.assertTrue(any(np.linalg.norm(matrix[:, 1]) > 0 for matrix in transfer['E'].values()))

    def test_aligned_differential_retarder_identity_with_unequal_amplitudes(self):
        flow = read_json(CONFIGS / 'flows/hybrid_mzi.json')
        params = read_json(CONFIGS / 'parameters/ideal.json')
        params['values'].update(ax=0.8, ay=1.3, retardance_c=0.5030303129643017, retardance_d=-0.6101096365046962)
        phase = np.linspace(0, 2 * np.pi, 57)
        actual = compile_network(from_dicts(flow, params)).evaluate({'phi2': phase})
        half_difference = (params['values']['retardance_c'] - params['values']['retardance_d']) / 2
        expected = (0.8**2 + 1.3**2) / 2 + (1.3**2 * np.cos(phase + half_difference) - 0.8**2 * np.cos(phase - half_difference)) / 2
        np.testing.assert_allclose(describe(actual['E'])['intensity'], expected, atol=1e-14)
        np.testing.assert_allclose(describe(actual['F'])['intensity'], 0.8**2 + 1.3**2 - expected, atol=1e-14)

    def test_contributor_study_separates_retardance_from_input_imbalance(self):
        flow = read_json(BASE / 'archive/effective_fits/configs/flows/hybrid_mzi_relative_fit.json')
        fitted = read_json(BASE / 'archive/effective_fits/configs/parameters/historical_fit.json')
        ideal = read_json(CONFIGS / 'parameters/ideal.json')
        study = contribution_study(flow, fitted, ideal)
        self.assertAlmostEqual(study['full']['ports']['E']['visibility'], 0.5052773708144573)
        self.assertLess(study['full']['total_phi2_peak_to_peak'], 1e-13)
        self.assertAlmostEqual(study['groups']['C/D retarders']['reset_to_ideal']['ports']['E']['visibility'], 0.1362331940276941)
        ratio = fitted['values']['ay'] / fitted['values']['ax']
        self.assertAlmostEqual(study['groups']['Input amplitude ratio']['alone_on_ideal']['ports']['E']['visibility'], abs(1-ratio**2)/(1+ratio**2))


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.flow=read_json(CONFIGS/'flows/hybrid_mzi.json')
        self.params=read_json(CONFIGS/'parameters/ideal.json')

    def compile(self):
        return compile_network(from_dicts(self.flow,self.params))

    def test_reject_invalid_expressions(self):
        for value in ['__import__("os")','pi.real','[1][0]','1/0','unknown',float('nan'),True]:
            with self.subTest(value=value),self.assertRaises(ConfigurationError):
                numeric(value)
        self.assertAlmostEqual(numeric('pi/4').real,np.pi/4)

    def test_reject_unknown_parameters_and_misspelled_keys(self):
        self.flow['elements'][0]['leakage_rad']='pbs_leakage_typo'
        with self.assertRaisesRegex(ConfigurationError,'Unknown parameter'):
            self.compile()
        self.flow['elements'][0]['leakage_rad']=0
        self.flow['elements'][0]['leakage']=0
        with self.assertRaisesRegex(ConfigurationError,'unknown keys'):
            self.compile()

    def test_reject_forward_references_and_implicit_duplication(self):
        self.flow['elements'][0]['inputs']=['future']
        with self.assertRaisesRegex(ConfigurationError,'forward input'):
            self.compile()
        self.flow['elements'][0]['inputs']=['Ein']
        self.flow['elements'][1]['inputs']=['Ein']
        with self.assertRaisesRegex(ConfigurationError,'already feeds'):
            self.compile()

    def test_reject_undeclared_terminal_and_gain(self):
        self.flow['outputs']=['E']
        with self.assertRaisesRegex(ConfigurationError,'terminal'):
            self.compile()
        self.flow['outputs']=['E','F']
        self.params['values']['loss_c']=1.1
        with self.assertRaisesRegex(ConfigurationError,'gain exceeds'):
            self.compile()

    def test_reject_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bad.json';path.write_text('{"name":1,"name":2}')
            with self.assertRaisesRegex(ConfigurationError,'duplicate'):
                read_json(path)

    def test_named_phase_checks_and_scan_bounds(self):
        self.params['phase_values']['ph1']=0
        with self.assertRaisesRegex(ConfigurationError,'exactly'):
            self.compile()
        del self.params['phase_values']['ph1']
        self.params['scan']['points']=1
        with self.assertRaisesRegex(ConfigurationError,'points'):
            self.compile()


class ArchivedFindingsTests(unittest.TestCase):
    def test_frozen_fit_reproduces_all_saved_detector_fringe_predictions(self):
        model=network('historical_fit','hybrid_mzi_relative_fit')
        fit=read_json(FIT_DIR/'physical-jones-fit/physical-jones-fit.json')
        with (FIT_DIR/'physical-jones-fit/jones-fringe-predictions.csv').open() as handle:
            rows=list(csv.DictReader(handle))
        phases=np.linspace(0,2*np.pi,101,endpoint=False)
        design=np.column_stack((np.ones_like(phases),np.cos(phases),np.sin(phases)))
        for row in rows:
            fields=model.evaluate({'phi1':float(row['phi1_command_rad']),'phi2':phases})
            for detector in ('pd','pax'):
                power=describe(fields[fit[detector+'_port']])['intensity']
                b,c,d=np.linalg.lstsq(design,fit[detector+'_offset']+fit[detector+'_gain']*power,rcond=None)[0]
                for suffix,value in [('baseline',b),('amplitude',np.hypot(c,d)),('predicted_contrast',np.hypot(c,d)/b)]:
                    self.assertAlmostEqual(value,float(row[detector+'_'+suffix]),places=12)

    def test_preserves_held_out_disagreement_in_existing_report(self):
        model=network('historical_fit','hybrid_mzi_relative_fit')
        fit=read_json(FIT_DIR/'physical-jones-fit/physical-jones-fit.json')
        saved=read_json(MAP_DIR/'physical-jones-validation/validation-summary.json')
        with (MAP_DIR/'data.csv').open() as handle:
            rows=list(csv.DictReader(handle))
        p1=np.array([float(r['phi1_rp_v']) for r in rows])*2*np.pi/(12.2/16.875)
        p2=np.array([float(r['phi2_rp_v']) for r in rows])*2*np.pi/.2
        obs={p:describe(f) for p,f in model.evaluate({'phi1':p1,'phi2':p2}).items()}
        errors={}
        for detector,column in [('pd','pd_mean_v'),('pax','pax_ptotal')]:
            measured=np.array([float(r[column]) for r in rows])
            predicted=fit[detector+'_offset']+fit[detector+'_gain']*obs[fit[detector+'_port']]['intensity']
            errors[detector]=np.sqrt(np.mean(((predicted-measured)/np.std(measured))**2))
        measured=np.array([[float(r[k]) for k in ('s1','s2','s3')] for r in rows])
        errors['stokes']=np.sqrt(np.mean((obs[fit['pax_port']]['stokes']-measured)**2))
        for key,value in errors.items():
            self.assertAlmostEqual(value,saved['power_normalized_rms'][key],places=7)


class ReportTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('pdflatex'),'pdflatex not available')
    def test_complete_pdf_snapshots_and_rerun(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'article'
            result=run(CONFIGS/'flows/single_element.json',CONFIGS/'parameters/single_element.json',output)
            self.assertTrue((output/'derivation.pdf').read_bytes().startswith(b'%PDF'))
            self.assertTrue((output/'symbolic-intensity.json').is_file())
            self.assertIn('a_x^2 A+a_y^2 B', (output/'derivation.tex').read_text())
            log=(output/'derivation.log').read_text()
            self.assertNotIn('Overfull',log)
            reloaded=compile_network(load_configuration(output/'flow.json',output/'parameters.json'))
            np.testing.assert_allclose(reloaded.evaluate()['out'],result['operating_point']['out']['jones'])
            with self.assertRaisesRegex(ConfigurationError,'not empty'):
                run(CONFIGS/'flows/single_element.json',CONFIGS/'parameters/single_element.json',output)


if __name__ == '__main__':
    unittest.main()
