"""Dashboard documentation content and accordion helper."""

import ipywidgets as widgets

_DOC_DESCRIPTION_HTML = """
<p>This application is an automated Python workflow driver designed to process crystal
structures from Crystallographic Information Files (CIFs) and run quantum mechanical energy
calculations using Quantum ESPRESSO (QE) on High-Performance Computing (HPC) clusters managed
by SLURM.</p>
<p><b>Capabilities:</b></p>
<ul>
<li><b>Automated Input Generation:</b> Leverages cif2cell to parse input CIF files and
construct valid Quantum ESPRESSO input scripts.</li>
<li><b>Automatic K-Point Grid Generation:</b> Calculates uniform Monkhorst-Pack k-point grids
directly from crystal cell dimensions (a, b, c) and a configurable target grid separation.
Alternatively user can provide K-Point grid manually.</li>
<li><b>Pseudopotential Management:</b> Automatically inspects atomic species in the structure,
matches them with corresponding .UPF files in a local directory.</li>
<li><b>Custom Input Parameter Injection:</b> Allows direct configuration of DFT functionals,
plane-wave energy cutoffs (Wavefunction Cutoff (Ry), Charge Density Cutoff (Ry)), occupations,
smearing parameters, and van der Waals dispersion corrections.</li>
<li><b>HPC SLURM Integration:</b> Writes custom SLURM batch execution scripts with configured
resource allocations and submits them via sbatch.</li>
<li><b>State Management &amp; Resumption:</b> Uses a persistent state .json tracker inside each
run context to enable crash recovery, task-level resumption, and atomic step tracking.</li>
<li><b>Batch Processing &amp; Result Aggregation:</b> Supports directory-wide multi-CIF
processing and collects output energies and execution statuses into consolidated CSV and JSON
summary reports. In case of calculation of multiple cif files of same molecule, sorts the results
from lowest to highest relative energies.</li>
</ul>
"""


_DOC_HOWTO_HTML = """
<p><b>Step 1: Input &amp; Calculation Selection</b></p>
<ul>
<li><b>Single/Multiple CIFs:</b> Upload a single CIF file, or provide path to directory storing
multiple CIFs.</li>
<li><b>Choose Calculation Type:</b> Select the desired Quantum ESPRESSO calculation mode (Single Point energy,
Fixed Cell Optimization, or Variable Cell Optimization) from the dropdown menu.</li>
</ul>
<p><b>Step 2: Pseudopotentials &amp; Structure Setup</b></p>
<ul>
<li><b>Pseudopotential Directory:</b> Provide the path to desired pseudopotential directory</li>
<li><b>K-Points Configuration:</b> Specify desired K-Point separation value (in Ry) to
automatically generate grid based on the cell parameter values (prefered). Or, manually specify
a grid in the K-Points Grid box (e.g., 3 3 2 0 0 0).</li>
</ul>
<p><b>Step 3: Electronic &amp; Dispersion Settings</b></p>
<ul>
<li>Adjust the electronic structure options in the Quantum ESPRESSO Settings panel</li>
<li><b>Exchange-Correlation Functional:</b> Automatically picked up from the pseudopotential
file. To override, enter manually in the DFT functional box (e.g., pbe, b86bpbe).</li>
<li><b>Energy Cutoffs:</b> Set the wavefunction cutoff and charge density cutoff in Ry.</li>
<li><b>Dispersion Corrections:</b> Choose a dispersion correction scheme (e.g., XDM,
grimme-d3).</li>
<li><b>Smearing:</b> Configure occupation types (e.g., smearing, fixed), smearing method (e.g.,
gaussian), and smearing width (degauss).</li>
</ul>
<p><b>Step 4: SLURM Job Settings &amp; Directory</b></p>
<ul>
<li><b>Runs Directory:</b> Choose the output workspace folder in the additional settings option,
where run logs, temporary input files, and state files will be saved.</li>
<li><b>SLURM Allocations:</b> Specify the requested Walltime (e.g., 12:00:00), Number of Core
Tasks (e.g., 8), and Quantum ESPRESSO executable command (default: pw.x).</li>
</ul>
<p><b>Step 5: Executing, Resuming, or Aggregating</b></p>
<ul>
<li><b>Starting a New Run:</b> Click Run Calculation to initiate input generation, staging, and
SLURM job submission.</li>
<li><b>Resuming an Interrupted Run:</b> Check the Resume Previous Run checkbox - Select a run
folder or enter a specific Run ID / Run Path to pick up from where execution stopped - Click
Resume Calculation. (place holder, to be updated after implementation)</li>
<li><b>Aggregating Results:</b> Navigate to the Results tab. View results by selecting job ids.
Download result as CSV file.</li>
</ul>
"""


_DOC_REFERENCES_HTML = """
<p><b>References</b></p>
<ul>
<li>P Giannozzi, O Andreussi, T Brumme, O Bunau, M Buongiorno Nardelli, M Calandra, R Car,
C Cavazzoni, D Ceresoli, M Cococcioni, N Colonna, I Carnimeo, A Dal Corso, S de Gironcoli,
P Delugas, R A DiStasio Jr, A Ferretti, A Floris, G Fratesi, G Fugallo, R Gebauer,
U Gerstmann, F Giustino, T Gorni, J Jia, M Kawamura, H-Y Ko, A Kokalj, E Küçükbenli,
M Lazzeri, M Marsili, N Marzari, F Mauri, N L Nguyen, H-V Nguyen, A Otero-de-la-Roza,
L Paulatto, S Poncé, D Rocca, R Sabatini, B Santra, M Schlipf, A P Seitsonen, A Smogunov,
I Timrov, T Thonhauser, P Umari, N Vast, X Wu and S Baroni, J.Phys.:Condens.Matter 29,
465901 (2017)</li>
<li>P. Giannozzi, S. Baroni, N. Bonini, M. Calandra, R. Car, C. Cavazzoni, D. Ceresoli,
G. L. Chiarotti, M. Cococcioni, I. Dabo, A. Dal Corso, S. Fabris, G. Fratesi,
S. de Gironcoli, R. Gebauer, U. Gerstmann, C. Gougoussis, A. Kokalj, M. Lazzeri,
L. Martin-Samos, N. Marzari, F. Mauri, R. Mazzarello, S. Paolini, A. Pasquarello,
L. Paulatto, C. Sbraccia, S. Scandolo, G. Sclauzero, A. P. Seitsonen, A. Smogunov,
P. Umari, R. M. Wentzcovitch, J. Phys. Condens. Matter 21, 395502 (2009)</li>
<li>(For GPU enabled Versions) P. Giannozzi, O. Baseggio, P. Bonfà, D. Brunato, R. Car,
I. Carnimeo, C. Cavazzoni, S. de Gironcoli, P. Delugas, F. Ferrari Ruffino, A. Ferretti,
N. Marzari, I. Timrov, A. Urru, S. Baroni;
<a href="https://doi.org/10.1063/5.0005082">J. Chem. Phys. 152, 154105 (2020)</a></li>
</ul>
<p><b>Contact</b></p>
<p>Reach out to Dr. Rahul Nikhar at
<a href="mailto:Rahul.Nikhar@pfizer.com">Rahul.Nikhar@pfizer.com</a> and
Dr. Krishnanjan Pramanik at
<a href="mailto:pkrishnanjan@aganitha.ai">pkrishnanjan@aganitha.ai</a> for questions related
to the application.</p>
"""


def _doc_accordion(title: str, body_html: str) -> widgets.Accordion:
    content = widgets.HTML(f'<div style="font-size:0.85rem;">{body_html}</div>')
    section = widgets.Accordion(children=[content])
    section.set_title(0, title)
    section.selected_index = None
    section.add_class("advqm-section")
    return section

