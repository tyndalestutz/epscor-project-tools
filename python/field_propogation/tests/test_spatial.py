"""Independent checks of temporary-probe inference and spatial propagation."""
from pathlib import Path
import unittest

import numpy as np

from field_propogation.configuration import ConfigurationError, read_json
from field_propogation.detection import gaussian_mode_overlaps, detected_outputs
from field_propogation.spatial_study import geometry, detectors, intensity_coefficients, evaluate_study

BASE=Path(__file__).resolve().parents[1]
POWERS=dict(AC=421,AD=343,BC=380,BD=380,ACE=215,ADE=151,
            BCE=194,BDE=172,ACF=180,ADF=183,BCF=169,BDF=201)


class SpatialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings=read_json(BASE/'configs/parameters/spatial_coupling_study.json')
        _,_,cls.networks,cls.results=evaluate_study(cls.settings,POWERS)

    def test_gaussian_overlap_matches_independent_transverse_integration(self):
        d=np.array([[.2,-.3],[-.8,.4],[.6,.2]])
        q=np.array([[.3,.5],[-.2,.7],[1.5,-.6]])
        gram,h=gaussian_mode_overlaps(d,q)
        axis=np.linspace(-7,7,401);x,y=np.meshgrid(axis,axis)
        profiles=np.array([np.sqrt(2/np.pi)*np.exp(-(x-dx)**2-(y-dy)**2)*np.exp(1j*(qx*x+qy*y))
                           for (dx,dy),(qx,qy) in zip(d,q)])
        reference=np.sqrt(2/np.pi)*np.exp(-x*x-y*y)
        cell=(axis[1]-axis[0])**2
        numerical=np.einsum('axy,bxy->ab',profiles.conj(),profiles)*cell
        projection=np.einsum('xy,axy->a',reference,profiles)*cell
        np.testing.assert_allclose(gram,numerical,atol=1e-12)
        np.testing.assert_allclose(h,projection,atol=1e-12)
        self.assertGreaterEqual(np.linalg.eigvalsh(gram).min(),-1e-12)
        self.assertGreaterEqual(np.linalg.eigvalsh(gram-np.outer(h.conj(),h)).min(),-1e-12)
        with self.assertRaises(ConfigurationError):gaussian_mode_overlaps([[1]],[[0]])
        with self.assertRaises(ConfigurationError):gaussian_mode_overlaps([[0,0]],[[np.nan,0]])

    def test_same_probe_efficiencies_do_not_fix_contrast(self):
        q=self.settings['gaussian_model']['d_arm_tilt_q']
        projection=[]
        for name in ('same_side','opposite_sides'):
            d,_,gram,h=geometry(self.settings,name,q)
            projection.append(abs(h)**2)
            expected=np.exp(-q*q/8)*abs(np.sin(q*(d[2,0]-d[0,0])/2))
            v=intensity_coefficients(self.networks['ideal'],gram)['E']['visibility']
            self.assertAlmostEqual(v,expected,places=14)
        np.testing.assert_allclose(*projection,atol=1e-15)
        self.assertLess(self.results['same_side']['splitter_cases']['C_reference']['E']['visibility'],.08)
        self.assertGreater(self.results['opposite_sides']['splitter_cases']['C_reference']['E']['visibility'],.7)

    def test_removed_probe_does_not_attenuate_free_space_fields(self):
        for name in ('same_side','opposite_sides'):
            _,_,gram,h=geometry(self.settings,name,0.)
            np.testing.assert_allclose(abs(h)**2,[.4,.4,.5,.5],atol=1e-15)
            obs=detected_outputs(self.networks['ideal'],{'phi2':np.linspace(0,2*np.pi,101)},detectors(gram))
            for port in ('E','F'):np.testing.assert_allclose(obs[port]['intensity'],1,atol=1e-14)
        gram=geometry(self.settings,'opposite_sides',0)[2]
        obs=detected_outputs(self.networks['ideal'],{'phi2':np.pi/2},detectors(gram))
        self.assertLess(obs['E']['dop'],.3)

    def test_harmonic_extrema_power_conservation_and_phi1_invariance(self):
        for network in self.networks.values():
            for name in ('same_side','opposite_sides'):
                for q in (0.,.3,1.5,3.):
                    gram=geometry(self.settings,name,q)[2]
                    coeff=intensity_coefficients(network,gram)
                    e=coeff['E'];k=complex(e['k_real'],e['k_imag'])
                    phases={'phi1':np.array([0.,1.,2.])[:,None],
                            'phi2':np.array([-np.angle(k),np.pi-np.angle(k)])[None,:]}
                    obs=detected_outputs(network,phases,detectors(gram))
                    np.testing.assert_allclose(obs['E']['intensity'],np.tile([e['maximum'],e['minimum']],(3,1)),atol=2e-14)
                    np.testing.assert_allclose(obs['E']['intensity']+obs['F']['intensity'],2,atol=2e-14)
                    for port in ('E','F'):
                        self.assertLessEqual(np.max(obs[port]['dop']),1+1e-12)
                        self.assertGreaterEqual(np.min(obs[port]['intensity']),-1e-12)

    def test_report_cross_coherency_expansion_matches_propagation(self):
        network=self.networks['C_reference']
        gram=geometry(self.settings,'opposite_sides',1.5)[2]
        v=network.config.parameters['values']
        angles=[v[key] for key in ('mu1_x','mu1_y','mu2_x','mu2_y')]
        tx,ty,ux,uy=np.cos(angles);rx,ry,vx,vy=np.sin(angles)
        a,b,c,d=tx*ux,rx*vx,ry*uy,ty*vy
        h,j,k,l=tx*vx,rx*ux,ty*uy,ry*vy
        phi1=.37;phi2=np.linspace(0,2*np.pi,41);z=np.exp(1j*phi2)
        # Gram order BD, BC, AD, AC; <left,right> = G[left,right].
        cross_e=-1j*np.exp(1j*phi1)*(a*c*gram[1,3]+a*d*z*gram[0,3]-b*c*z.conj()*gram[1,2]-b*d*gram[0,2])
        cross_f=1j*np.exp(1j*phi1)*(h*k*z*gram[0,3]-h*l*gram[1,3]+j*k*gram[0,2]-j*l*z.conj()*gram[1,2])
        obs=detected_outputs(network,{'phi1':phi1,'phi2':phi2},detectors(gram))
        for port,cross in [('E',cross_e),('F',cross_f)]:
            np.testing.assert_allclose(obs[port]['stokes'][...,1]*obs[port]['intensity'],2*cross.real,atol=1e-14)
            np.testing.assert_allclose(obs[port]['stokes'][...,2]*obs[port]['intensity'],2*cross.imag,atol=1e-14)


if __name__=='__main__':unittest.main()
