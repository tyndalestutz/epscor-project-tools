"""Publication-style LaTeX derivation and vector plots from propagated fields.

No model-specific final-field or Stokes formula is inserted into the report.
The same phase coefficients drive numerical evaluation and the derivation.
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import numpy as np

from .observables import STOKES_MATRICES, state_label
from .propagation import Network
from .symbolic_intensity import symbolic_intensity_data
from .provenance import interpretation
from .configuration import numeric


def escape(text: object) -> str:
    replacements = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$',
                    '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    return ''.join(replacements.get(c, c) for c in str(text))


def number(value: float | None, digits: int = 5) -> str:
    if value is None or not np.isfinite(value):
        return r'\text{undefined}'
    if value == 0:
        return '0'
    result = f'{value:.{digits}g}'
    if 'e' in result:
        mantissa, exponent = result.split('e')
        return mantissa + r'\times 10^{' + str(int(exponent)) + '}'
    return result


def coefficient(value: complex) -> str:
    """Compact fractions for recognizable coefficients, decimals otherwise."""
    def part(x):
        rational = Fraction(float(x)).limit_denominator(16)
        if rational.numerator and abs(float(rational)-x) < 1e-13:
            if rational.denominator == 1:
                return str(rational.numerator)
            return ('-' if x < 0 else '') + rf'\frac{{{abs(rational.numerator)}}}{{{rational.denominator}}}'
        return number(x)
    if not np.isfinite(value):
        return r"\text{undefined}"
    a, b = complex(value).real, complex(value).imag
    tolerance = max(abs(value), np.finfo(float).tiny) * 1e-13
    if abs(b) <= tolerance:
        return part(a)
    imag = ('i' if abs(abs(b)-1) < 1e-13 else part(abs(b)) + 'i')
    if abs(a) <= tolerance:
        return ('-' if b < 0 else '') + imag
    return part(a) + (' - ' if b < 0 else ' + ') + imag


def matrix(values: np.ndarray) -> str:
    return r'\begin{bmatrix}' + r'\\'.join(' & '.join(coefficient(v) for v in row)
                        for row in np.atleast_2d(values)) + r'\end{bmatrix}'


def field(name: str) -> str:
    return r'\mathbf{E}_{\mathrm{' + escape(name) + '}}'


def phase_label(name: str) -> str:
    if name.startswith('phi') and name[3:].isdigit():
        return r'\phi_{' + name[3:] + '}'
    return r'\varphi_{\mathrm{' + escape(name) + '}}'


def mode_label(mode: tuple, phases: tuple) -> str:
    result = ''
    for order, phase in zip(mode, phases):
        if order:
            term = (str(abs(order)) if abs(order) != 1 else '') + phase_label(phase)
            result += ('-' if order < 0 else '+' if result else '') + term
    return result or '0'


def positive(mode: tuple) -> bool:
    return next((k > 0 for k in mode if k), False)


def equation(body: str) -> str:
    return '\n\\begin{equation}\n' + body + '\n\\end{equation}\n'


def table(headers: list[str], rows: list[list[str]], columns: str | None = None,
          *, row_gap: str = "") -> str:
    columns = columns or ('l' + 'r' * (len(headers)-1))
    return ('\n\\begin{center}\n\\begin{tabular}{' + columns + '}\n\\toprule\n' +
            ' & '.join(headers) + r'\\ \midrule' + '\n' +
            '\n'.join(' & '.join(row) + r'\\' + ('[' + row_gap + ']' if row_gap else '') for row in rows) +
            '\n\\bottomrule\n\\end{tabular}\n\\end{center}\n')


def write_plots(network: Network, scans: dict, output: Path) -> list[tuple[str, str]]:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'serif', 'font.size': 10, 'axes.spines.top': False,
                         'axes.spines.right': False, 'savefig.bbox': 'tight'})
    figures = []
    x = scans['axis'] / np.pi
    for index, (phase, ports) in enumerate(scans['cuts'].items()):
        fig, axes = plt.subplots(len(ports), 2, figsize=(9, 2.7*len(ports)), squeeze=False, layout='constrained')
        for row, (port, obs) in enumerate(ports.items()):
            axes[row, 0].plot(x, obs['intensity'], color='#244e77', lw=1.8)
            axes[row, 0].set(title=f'Port {port}: intensity', ylabel='Intensity (field units squared)')
            for j, color in enumerate(('#244e77', '#b85b24', '#38836a')):
                axes[row, 1].plot(x, obs['stokes'][..., j], label=f'$s_{j+1}$', color=color, lw=1.5)
            axes[row, 1].set(title=f'Port {port}: normalized Stokes', ylim=(-1.07, 1.07))
            axes[row, 1].legend(ncol=3, fontsize=9)
            for ax in axes[row]:
                ax.set_xlabel('$' + phase_label(phase) + r'/\pi$')
                ax.grid(alpha=.16)
        held = [(name, value) for name, value in network.config.phase_values.items() if name != phase]
        fixed = ', '.join('$' + phase_label(name) + '=' + f'{value:.5g}' + r'\,\mathrm{rad}$' for name, value in held)
        if held:
            fig.suptitle('Held fixed: ' + fixed, fontsize=11)
        filename=f'phase-cut-{index+1}.pdf'
        fig.savefig(output/filename); plt.close(fig)
        caption = f'One-axis scan of {phase}. Held fixed: ' + (', '.join(f'{name} = {value:.5g} rad' for name, value in held) or 'no other phase controls') + '. Other phases are not averaged; component amplitudes need not match another scan. Gaps mark undefined dark-port polarization.'
        figures.append((filename, caption))
    for index, (phases, ports) in enumerate(scans['maps'].items()):
        fig, axes = plt.subplots(1, len(ports), figsize=(4.2*len(ports), 3.5), squeeze=False, layout='constrained')
        for ax, (port, obs) in zip(axes[0], ports.items()):
            data=obs['intensity']; low, high=float(data.min()),float(data.max())
            # A numerically constant ideal map should not amplify round-off.
            if high-low < 1e-10*max(high, 1):
                low,high=max(0,low-.05*max(high,1)),high+.05*max(high,1)
            im=ax.imshow(data,origin='lower',extent=(x[0],x[-1],x[0],x[-1]),aspect='auto',cmap='viridis',vmin=low,vmax=high)
            ax.set(title=f'Port {port}: intensity',xlabel='$'+phase_label(phases[0])+r'/\pi$',ylabel='$'+phase_label(phases[1])+r'/\pi$')
            fig.colorbar(im,ax=ax,shrink=.85)
        filename=f'phase-map-{index+1}.pdf'
        fig.savefig(output/filename);plt.close(fig)
        figures.append((filename,f'Joint intensity map for {phases[0]} and {phases[1]}; remaining phases are fixed at the operating point. Color scales are labeled separately.'))
    return figures



def amplitude_section(network: Network, data: dict) -> str:
    """Typeset the input-amplitude quadratic before numerical substitutions."""
    parts = [r'''\section{Intensity with independent input amplitudes}
Let $a_x,a_y\ge0$ be independent amplitudes and $\delta$ their relative input
phase. A common global input phase is immaterial for all observables:
\begin{equation}
\mathbf E_{\mathrm{in}}=\begin{bmatrix}a_x e^{i\delta}\\a_y\end{bmatrix}.
\end{equation}
Keep the configured optical element values fixed and propagate the two unit
basis inputs separately. For each output this defines a transfer matrix
$M=[\mathbf u,\mathbf v]$ and the actual field
\begin{equation}
\mathbf E=a_xe^{i\delta}\mathbf u(\boldsymbol\phi)
+a_y\mathbf v(\boldsymbol\phi).
\end{equation}
No equality of the input amplitudes has been used. Expanding the squared norm,
\begin{align}
I&=(a_xe^{-i\delta}\mathbf u^\dagger+a_y\mathbf v^\dagger)
(a_xe^{i\delta}\mathbf u+a_y\mathbf v)\\
&=a_x^2 A+a_y^2 B+2a_xa_y\operatorname{Re}(e^{-i\delta}G),\\
A&=\mathbf u^\dagger\mathbf u,\qquad
B=\mathbf v^\dagger\mathbf v,\qquad G=\mathbf u^\dagger\mathbf v,\\
I&=a_x^2 A+a_y^2 B+2a_xa_y[\operatorname{Re}G\cos\delta+
\operatorname{Im}G\sin\delta].
\end{align}
The following tables define these functions for the configured optics while
$a_x$, $a_y$, and $\delta$ remain symbolic. Element phase offsets are retained
inside $M$; they are not silently reassigned to the input phase.
If a parameter name is shared by an input and an optic, the optic remains at
its configured value in this input-only variation.

Write $M=\sum_k M_k e^{i\mathbf k\cdot\boldsymbol\phi}$.
Then $M^\dagger M=\sum_d W_d e^{i\mathbf d\cdot\boldsymbol\phi}$ with
\begin{equation}
W_d=\sum_{l-k=d}M_k^\dagger M_l.
\end{equation}
Its diagonal entries give $A,B$ and its off-diagonal entry gives $G$.
Each real function $f\in\{A,B,\operatorname{Re}G,\operatorname{Im}G\}$ is
specified by the constant, cosine and sine coefficients in its table column:
\begin{equation}
f=f_0+\sum_{d>0}[f_{c,d}\cos(\mathbf d\cdot\boldsymbol\phi)
+f_{s,d}\sin(\mathbf d\cdot\boldsymbol\phi)].
\end{equation}
These coefficients are computed from the optical transfer matrices, not fitted
to a power curve. Displayed coefficients are rounded; the accompanying
\texttt{symbolic-intensity.json} retains full numerical precision.
''']
    for port, item in data.items():
        parts.append(r'\subsection{Configured output ' + escape(port) + '}')
        rows = []
        for entry in item['transfer_modes']:
            transfer = entry['matrix']
            rows.append([
                '$' + mode_label(entry['mode'], network.phases) + '$',
                '$' + matrix(transfer[:, :1]) + '$',
                '$' + matrix(transfer[:, 1:]) + '$',
            ])
        parts.append(table([r'$\mathbf k\cdot\boldsymbol\phi$', r'$\mathbf u_k$', r'$\mathbf v_k$'], rows, row_gap='7pt'))
        modes = {entry['mode']: entry['coefficients'] for entry in item['intensity_form_modes']}
        threshold = max(np.max(np.abs(value)) for value in modes.values()) * 1e-12
        def values_row(label, values):
            return [label] + ['$' + number(0.0 if abs(v) <= threshold else float(v)) + '$' for v in values]
        rows = [values_row('constant', modes[(0,) * len(network.phases)].real)]
        for mode, value in sorted(modes.items()):
            if not positive(mode):
                continue
            for kind, values in [('cos', 2 * value.real), ('sin', -2 * value.imag)]:
                if np.max(np.abs(values)) > threshold:
                    rows.append(values_row('$\\' + kind + '(' + mode_label(mode, network.phases) + ')$', values))
        parts.append(table(['term', '$A$', '$B$', r'$\operatorname{Re}G$', r'$\operatorname{Im}G$'], rows))
        parts.append(equation(r'I_{\mathrm{' + escape(port) + r'}}=a_x^2 A+a_y^2 B'
                             r'+2a_xa_y[\operatorname{Re}G\cos\delta+\operatorname{Im}G\sin\delta].'))
    source = next(iter(network.fields[network.config.flow['input']['name']].values()))
    delta = float(np.angle(source[0] * source[1].conjugate()))
    parts.append('Only the numerical plots and operating-point results substitute '
                 '$a_x=' + number(float(abs(source[0]))) + '$ and $a_y='
                 + number(float(abs(source[1]))) + r'$, with $\delta='
                 + number(delta) + '$ rad (an arbitrary relative phase if either amplitude is zero).')
    if set(network.phases) == {'phi1', 'phi2'} and set(network.outputs) == {'E', 'F'}:
        parts.append((Path(__file__).parent / 'templates/ideal_amplitude_reference.tex').read_text())
    return '\n'.join(parts)


def write_report(network: Network, summary: dict, figures: list, provenance: dict, output: Path,
                 *, amplitude_data: dict | None = None) -> Path:
    """Write a standalone article; all displayed final expressions are derived."""
    flow, parameters=network.config.flow,network.config.parameters
    if amplitude_data is None:
        amplitude_data = symbolic_intensity_data(network)
    parts=[r'''\documentclass[11pt,a4paper]{article}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage[margin=22mm]{geometry}
\usepackage{amsmath,amssymb,booktabs,graphicx,microtype,longtable,xcolor,hyperref,array}
\hypersetup{colorlinks=true,linkcolor=blue,urlcolor=blue}
\setlength{\parindent}{0pt}
\setlength{\parskip}{5pt}
\setlength{\emergencystretch}{2em}
\allowdisplaybreaks
\title{Jones-field propagation and polarization analysis\\[4pt]\large '''+escape(flow['name'])+r'''}
\author{Reproducible optical simulation}
\date{'''+escape(parameters.get('name','Configured parameters'))+r'''}
\begin{document}
\maketitle
\begin{abstract}
This calculation follows coherent Jones fields through the declared optical
network. It derives the output intensity and Stokes parameters from the same
complex amplitudes used in the numerical evaluation. Phase scans distinguish
intensity modulation from a change in polarization at constant intensity.
Input amplitudes are retained as independent symbols before numerical
substitution. Optical coefficients use the selected parameter file; phase
variables remain analytic. Intensity is in squared input-field units.
\end{abstract}
''']
    parts.append(r'\begin{center}\fbox{\parbox{.93\linewidth}{' + escape(interpretation(parameters)) + r'}}\end{center}')
    parts += [escape(flow.get('description','')), '\n\n'+escape(parameters.get('description','')),
              r'\section{Operating point and output state}']
    if network.phases:
        parts.append(equation(r',\qquad '.join(phase_label(p)+'='+number(v) for p,v in network.config.phase_values.items())+r'\quad\mathrm{rad}.'))
    else: parts.append('This configuration contains no scanned phase controls.')
    for port,obs in summary['operating_point'].items():
        parts.append(r'\subsection*{Output '+escape(port)+'}')
        parts.append(equation(field(port)+'='+matrix(obs['jones'][:,None])+r',\qquad I='+number(float(obs['intensity']))+'.'))
        parts.append('Expected state: '+escape(state_label(obs))+'.')
        parts.append(equation(r'\mathbf{s}='+matrix(obs['stokes'][:,None])+r',\quad \theta='+number(float(obs['theta_rad']))+r',\quad\eta='+number(float(obs['eta_rad']))+r'\quad\mathrm{rad}.'))
        parts.append(equation(r'u='+number(float(obs['u_rad']))+r',\quad v='+number(float(obs['v_rad']))+r'\quad\mathrm{rad}.'))
    parts.append(r'''\section{Field, Stokes, and ellipse conventions}
The Jones basis is $(x,y)$ and fields are column vectors. The repository uses
$Q_1=|E_y|^2-|E_x|^2$, with the transmitted output listed first at a beam
splitter. These conventions preserve the existing hybrid-MZI reference;
confirm the physical output port before comparing to a PAX measurement.
Unnormalized Stokes quantities are denoted $Q_j$ here, and normalized
coordinates are $s_j$. Older repository reports call the normalized values
$S_1,S_2,S_3$.
\begin{align}
I=Q_0&=E_x^*E_x+E_y^*E_y,\\
Q_1&=E_y^*E_y-E_x^*E_x,\\
Q_2&=E_xE_y^*+E_x^*E_y=2\operatorname{Re}(E_xE_y^*),\\
Q_3&=(E_xE_y^*-E_x^*E_y)/i=2\operatorname{Im}(E_xE_y^*),\\
s_j&=Q_j/Q_0\quad(j=1,2,3).
\end{align}
Thus intensity and polarization follow directly from the final Jones vector.
The corresponding PAX-style ellipse angles and S1-polar sphere angles are
\begin{align}
\theta&=\tfrac12\operatorname{atan2}(s_2,s_1), &
\eta&=\tfrac12\arcsin(s_3),\\
u&=\operatorname{atan2}(s_3,s_2), & v&=\arccos(s_1).
\end{align}
Here $\theta$ is referenced to the positive $y$ axis under this Stokes
convention; it is defined modulo $\pi$. The signed minor-to-major axis ratio
is $\tan\eta$. Linear states have $s_3=0$; circular states have $|s_3|=1$;
other nonzero Jones fields are elliptical. Right/left handedness requires an
observer and time convention and is not assigned by this report.
A coherent, nonzero Jones field obeys $s_1^2+s_2^2+s_3^2=1$.
At a dark port all normalized polarization quantities are undefined;
$\theta$ is also undefined for circular polarization and $u$ at an S1 pole.
''')
    parts.append(r'Numerically, polarization is masked when $I\le '+number(network.config.scan['dark_threshold'])+'$. This is an absolute threshold in the configured intensity units.')
    parts.append(amplitude_section(network, amplitude_data))
    parts.append(r'\section{Optical flow and element-by-element derivation}')
    source=flow['input']['name']
    parts.append(equation(field(source)+'='+matrix(next(iter(network.fields[source].values()))[:,None])+'.'))
    parts.append(r'''Each element acts on the field produced by the preceding step.
A retarder or diattenuator uses
\begin{equation}
J=R(\gamma)^T
\begin{bmatrix}\tau_xe^{i\beta_x}&0\\0&\tau_ye^{i\beta_y}\end{bmatrix}R(\gamma),\qquad
R(\gamma)=\begin{bmatrix}\cos\gamma&-\sin\gamma\\\sin\gamma&\cos\gamma\end{bmatrix}.
\end{equation}
A lossless retarder sets $\tau_x=\tau_y=1$ and
$(\beta_x,\beta_y)=(-\rho/2,\rho/2)$.
For an NPBS, $(E_t,E_r)=(TA+RB,RA+TB)$ with
$T=\operatorname{diag}(\cos\mu_x,\cos\mu_y)$ and
$R=i\operatorname{diag}(\sin\mu_x,\sin\mu_y)$.
The PBS leakage model sends a single incident field into
$\operatorname{diag}(\cos\epsilon,i\sin\epsilon)E$ and
$\operatorname{diag}(i\sin\epsilon,\cos\epsilon)E$.
These are the declared component conventions, not inferred Fresnel coefficients.
The following matrices have the chosen parameter values substituted.
''')
    for index,step in enumerate(network.steps,1):
        parts.append(r'\subsection*{'+str(index)+'. '+escape(step.name)+' ('+escape(step.kind)+')}')
        for target,blocks in zip(step.outputs,step.blocks):
            terms=[matrix(block)+field(src) for block,src in zip(blocks,step.inputs)]
            expression='+'.join(terms)
            if step.phase:
                expression='e^{i'+phase_label(step.phase)+'}'+r'\left('+expression+r'\right)'
            parts.append(equation(field(target)+'='+expression+'.'))
        if step.allow_gain:
            parts.append('This step explicitly permits a relative amplitude scale greater than one. It is not constrained to a passive transmission.')
    parts.append(r'''\section{Analytic output fields and Stokes derivation}
Collecting paths with the same phase dependence gives a finite coherent sum:
\begin{equation}
\mathbf E(\boldsymbol\phi)=\sum_k\mathbf a_k e^{i\mathbf k\cdot\boldsymbol\phi},\qquad
\mathbf a_k=\begin{bmatrix}a_{x,k}\\a_{y,k}\end{bmatrix}.
\end{equation}
The tables below specify each final Jones vector explicitly. Multiplication
of the field by its conjugate gives the Stokes harmonics, not an empirical fit:
\begin{align}
Q_j&=\mathbf E^\dagger H_j\mathbf E
=\sum_{k,l}\mathbf a_k^\dagger H_j\mathbf a_l\,
 e^{i(\mathbf l-\mathbf k)\cdot\boldsymbol\phi},\\
c_{j,\mathbf d}&=\sum_{\mathbf l-\mathbf k=\mathbf d}\mathbf a_k^\dagger H_j\mathbf a_l.
\end{align}
''')
    parts.append(equation(r',\quad '.join('H_'+str(j)+'='+matrix(h) for j,h in enumerate(STOKES_MATRICES))+'.'))
    parts.append(r'''Because $c_{j,-\mathbf d}=c_{j,\mathbf d}^*$, the result is real:
\begin{align}
Q_j&=B_j+\sum_{\mathbf d>0}\left[
 C_{j,\mathbf d}\cos(\mathbf d\cdot\boldsymbol\phi)
 +D_{j,\mathbf d}\sin(\mathbf d\cdot\boldsymbol\phi)\right],\\
B_j&=c_{j,\mathbf0},\qquad
C_{j,\mathbf d}=2\operatorname{Re}c_{j,\mathbf d},\qquad
D_{j,\mathbf d}=-2\operatorname{Im}c_{j,\mathbf d}.
\end{align}
The notation $\mathbf d>0$ selects modes whose first nonzero entry is positive.
The constant and cosine/sine coefficient tables are explicit formulas for
$I=Q_0$ and each numerator of $s_j=Q_j/Q_0$. Coefficients are displayed to five
significant figures; JSON and CSV retain numerical precision. Terms below
$10^{-12}$ of the largest output harmonic are omitted from typesetting only.
''')
    for port in network.outputs:
        parts.append(r'\subsection{Output '+escape(port)+'}')
        modes=network.fields[port]
        rows=[['$'+mode_label(mode,network.phases)+'$', '$'+coefficient(a[0])+'$', '$'+coefficient(a[1])+'$'] for mode,a in sorted(modes.items())]
        parts.append(table([r'$\mathbf k\cdot\boldsymbol\phi$', '$a_{x,k}$','$a_{y,k}$'],rows))
        harmonics=network.stokes_harmonics(port)
        zero=(0,)*len(network.phases)
        baseline=harmonics.get(zero,np.zeros(4)).real
        scale=max((float(np.max(np.abs(c))) for c in harmonics.values()),default=0)
        threshold=scale*1e-12
        rows=[['constant']+['$'+number(0.0 if abs(v) <= threshold else float(v))+'$' for v in baseline]]
        for mode,c in sorted(harmonics.items()):
            if not positive(mode):
                continue
            for kind,values in [('cos',2*c.real),('sin',-2*c.imag)]:
                if np.max(np.abs(values)) > threshold:
                    rows.append(['$\\'+kind+'('+mode_label(mode,network.phases)+')$']+['$'+number(0.0 if abs(v) <= threshold else float(v))+'$' for v in values])
        parts.append(table(['term','$Q_0=I$','$Q_1$','$Q_2$','$Q_3$'],rows))
        # Show compact scalar expressions as well as the shared coefficient table.
        for j in range(4):
            terms=[number(0.0 if abs(baseline[j]) <= threshold else float(baseline[j]))]
            for mode,c in sorted(harmonics.items()):
                if not positive(mode):
                    continue
                for kind,value in [('cos',2*c[j].real),('sin',-2*c[j].imag)]:
                    if abs(value)>threshold:
                        terms.append((' - ' if value < 0 else ' + ')+number(float(abs(value)))+'\\'+kind+'('+mode_label(mode,network.phases)+')')
            # Break long Fourier sums into align lines rather than shrinking text.
            chunks=[''.join(terms[i:i+2]) for i in range(0,len(terms),2)]
            body='Q_{'+str(j)+'}&='+chunks[0]
            for chunk in chunks[1:]:
                body+=r'\\ &\quad '+chunk
            parts.append(equation(r'\begin{aligned}' + body + r'\end{aligned}'))
        parts.append(equation(r'I_{\mathrm{'+escape(port)+r'}}=Q_0,\qquad\mathbf s_{\mathrm{'+escape(port)+r'}}=\frac1{Q_0}\begin{bmatrix}Q_1\\Q_2\\Q_3\end{bmatrix}.'))
    parts.append(r'''\section{Phase dependence and sensitivity}
For each phase $\phi_m$, differentiation of the propagated field gives
\begin{align}
\partial_m\mathbf E&=\sum_k i k_m\mathbf a_k e^{i\mathbf k\cdot\boldsymbol\phi},\\
\partial_m Q_j&=2\operatorname{Re}\left(\mathbf E^\dagger H_j\partial_m\mathbf E\right),\\
\partial_m s_j&=\frac{\partial_m Q_j-s_j\partial_m I}{I}.
\end{align}
The norm $\|\partial_m\mathbf s\|$ measures polarization motion on the unit
sphere per radian of phase. It avoids jumps caused by wrapping ellipse angles.
Large $|\partial_m I|$ indicates power sensitivity; large
$\|\partial_m\mathbf s\|$ can occur even when intensity is constant.
The reported visibility is $(I_{\max}-I_{\min})/(I_{\max}+I_{\min})$ on the
sampled one-axis cut. These finite-grid extrema are descriptive, not certified
global bounds. Near a dark port normalized polarization can be ill-conditioned.
''')
    parts.append('Each cut uses '+str(network.config.scan['points'])+' samples from $'+number(network.config.scan['start_rad'])+'$ to $'+number(network.config.scan['stop_rad'])+'$ rad; all other phases remain fixed.')
    rows=[]
    for phase,ports in summary['phase_cuts'].items():
        for port,s in ports.items():
            rows.append([escape(port),'$'+phase_label(phase)+'$']+['$'+number(s[k])+'$' for k in ('min_intensity','max_intensity','sampled_visibility','max_abs_dI_dphase','max_stokes_speed')])
    if rows:
        parts.append(table(['port','phase',r'$I_{\min}$',r'$I_{\max}$','visibility',r'$\max|\partial I|$',r'$\max\|\partial\mathbf s\|$'],rows,'llrrrrr'))
    for filename,caption in figures:
        parts.append(r'\begin{figure}[p]\centering\includegraphics[width=\linewidth,height=.78\textheight,keepaspectratio]{'+filename+r'}\caption{'+escape(caption)+r'}\end{figure}')
    parts.append(r'\clearpage\section{Reproducibility and model scope}')
    parts.append(r'''This is a monochromatic, fully coherent Jones calculation for one spatial
mode per path. It does not model partial polarization, spectral averaging,
spatial amplitude maps, detector response, or mechanical hysteresis. Principal-axis
amplitude and phase response can be entered directly; spatial distributions
require an explicit spatial-mode extension. Optical intensity is not a PD
voltage or PAX power until a separate detector calibration is applied.
The saved configuration snapshots and harmonic coefficients accompany this report.
''')
    for key,value in provenance.items():
        parts.append('\n'+escape(key)+':\\\\\n{\\small\\texttt{'+escape(value)+'}}\\par\n')
    parts.append(r'\section{Parameter provenance}')
    parts.append('Full-precision values and full measurement IDs are in \\texttt{parameter-provenance.csv}. '
                 'Source prefixes below identify the registered independent observations. '
                 'An uncertainty is a supplied reduction estimate, not proof of identifiability or a propagated output confidence interval.')
    parts.append(r'\small\begin{longtable}{>{\raggedright\arraybackslash}p{.22\linewidth}>{\raggedright\arraybackslash}p{.23\linewidth}>{\raggedright\arraybackslash}p{.46\linewidth}}'
                 r'\toprule Parameter / status & Value / units / uncertainty & Plane / source / meaning \\ \midrule \endhead')
    for name, value in parameters['values'].items():
        item = parameters.get('provenance', {}).get(name, {})
        status = item.get('status', 'unverified')
        color = {'measured': 'green!40!black', 'derived': 'blue', 'convention': 'black',
                 'nuisance': 'orange!60!black', 'assumed': 'orange!60!black', 'fitted_nuisance': 'red'}.get(status, 'red')
        source = ', '.join(identifier[:12] for identifier in item.get('sources', [])) or 'none'
        sigma = item.get('uncertainty')
        resolved = numeric(value, parameters['values'])
        display = '$' + number(resolved.real) + '$'
        if resolved.imag:
            display += r'\newline $+(' + number(resolved.imag) + ')i$'
        parts.append(escape(name).replace(r'\_', r'\_\allowbreak ') + r'\newline\textcolor{' + color + '}{' + escape(status.upper()).replace(r'\_', r'\_\allowbreak ') + '} & '
                     + display + r'\newline ' + escape(item.get('units', 'unspecified')) + r'\newline $\sigma=' + number(sigma) + '$ & '
                     + escape(item.get('plane', 'unspecified')) + r'\newline Sources: ' + escape(source)
                     + r'\newline ' + escape(item.get('quantity', 'unverified quantity'))
                     + r'\newline ' + escape(item.get('notes', 'No independent provenance supplied.')) + r'\\[5pt]')
    parts.append(r'\bottomrule\end{longtable}\normalsize')
    parts.append(r'Original numeric expressions are preserved in \texttt{parameters.json}; the table above displays evaluated values.')
    if network.unused_parameters:
        parts.append('Unused parameters: '+escape(', '.join(network.unused_parameters))+'.')
    parts.append('\n\\end{document}\n')
    path=output/'derivation.tex'
    path.write_text('\n'.join(parts))
    return path
