# User requirements for field-propagation work

- Keep studies compact: one editable parameter/scenario JSON per study, reuse the shared propagation engine, and keep generated artifacts out of Git.
- Reports must be standalone, typeset PDF derivations with the established `templates/splitter_derivation.tex` presentation quality.
- Carry named field coefficients symbolically from the input through each optical element. Expand intensity and Stokes products before numerical substitution; retain independent ax/ay until the requested specialization.
- Trace every numerical coefficient to a measured value or an explicitly labeled baseline/scenario assumption. Do not fit missing physical parameters to final fringes unless the user asks.
- Derive visibility from extrema and state decimal versus percent, phase units, held-fixed scan values, power normalization and detector observable. Explain phase angles versus splitter-ratio parameterizations.
- Include readable comparison plots, numerical checks against direct propagation, and explicit limitations. Compile and visually inspect the PDF before delivering it.
- Temporary alignment probes do not insert loss into the operating interferometer. Distinguish probe overlap information from actual receiver projection and from free-space collected power.
