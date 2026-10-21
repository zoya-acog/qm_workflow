"""Dashboard documentation content and accordion helper."""

import html

import ipywidgets as widgets
from advQMcalc_test.site_config import SITE

# Shown under "Contact". A site sets its own in config.yaml:
#   app:
#     contact:
#       - {name: Dr. A, email: a@example.com}
# An empty list hides the Contact section.
_DEFAULT_CONTACTS = [
    {"name": "Dr. Rahul Nikhar", "email": "Rahul.Nikhar@pfizer.com"},
    {"name": "Dr. Krishnanjan Pramanik", "email": "pkrishnanjan@aganitha.ai"},
]


def _contact_html(contacts: list[dict]) -> str:
    if not contacts:
        return ""
    people = [
        f'{html.escape(c["name"])} at <a href="mailto:{html.escape(c["email"])}">{html.escape(c["email"])}</a>'
        for c in contacts
    ]
    joined = people[0] if len(people) == 1 else ", ".join(people[:-1]) + " and " + people[-1]
    return (f"<p><b>Contact</b></p>\n<p>Reach out to {joined} for questions related "
            "to the application.</p>\n")

_DOC_DESCRIPTION_HTML = """
<p>This application is an automated workflow driver designed to process crystal structures from
Crystallographic Information Files (CIFs) and run density functional theory based quantum
mechanical energy calculations using Quantum ESPRESSO (QE) on High-Performance Computing (HPC)
clusters.</p>
<p><b>Capabilities:</b></p>
<ul>
<li><b>Automated Input Generation:</b> Leverages cif2cell to parse input CIF files and
construct valid Quantum ESPRESSO input scripts.</li>
<li><b>Automatic K-Point Grid Generation:</b> Calculates uniform Monkhorst-Pack k-point grids
directly from crystal cell dimensions (a, b, c) and a configurable target grid separation.
Alternatively user can provide K-Point grid manually.</li>
<li><b>Pseudopotential Management:</b> Automatically inspects atomic species in the structure,
matches them with corresponding pseudopotential files in an accessible directory.</li>
<li><b>Custom Input Parameter Injection:</b> Allows configuration of DFT functionals,
plane-wave energy cutoffs (Wavefunction Cutoff (Ry), Charge Density Cutoff (Ry)), occupations,
smearing parameters, and van der Waals dispersion corrections.</li>
<li><b>HPC SLURM Integration:</b> Writes custom SLURM batch execution scripts with configured
resource allocations and submits them via sbatch. Multi CIF file jobs are handled with slurm
array.</li>
<li><b>State Management &amp; Resumption:</b> Uses a persistent state .json tracker inside each
run context to enable crash recovery, task-level resumption, and atomic step tracking.</li>
<li><b>Batch Processing &amp; Result Aggregation:</b> Supports directory-wide multi-CIF
processing and collects output energies and execution statuses into consolidated CSV and JSON
summary reports. In case of calculation of multiple cif files of same molecule, sorts the results
from lowest to highest relative energies. User can download CSV file, structure files from fixed
and variable cell optimization, QE output files.</li>
</ul>
"""


_DOC_HOWTO_HTML = """
<p><b>Step-by-Step Guide</b></p>
<p><b>New Calculation</b></p>
<ul>
<li><b>Single/Multiple CIFs:</b> Upload a single CIF file, or provide absolute path to directory
storing multiple CIFs.</li>
<li><b>Choose Calculation Type:</b> Select the desired Quantum ESPRESSO calculation mode (e.g.,
single point energy (scf), fixed cell optimization (relax), variable cell optimization
(vc-relax)) from the dropdown menu.</li>
<li><b>Pseudopotential Directory:</b> Provide the absolute path to desired pseudopotential
directory</li>
<li><b>K-Points Configuration:</b> Specify desired k-Point separation value (in 1/Å) to
automatically generate grid based on the cell parameter values (preferred). Or, manually specify
a grid in the k-Points Grid box (e.g., 3 3 2 0 0 0).</li>
<li><b>Exchange-Correlation Functional:</b> Automatically picked up from the pseudopotential
file. To override, enter manually in the DFT functional box (e.g., pbe, b86bpbe).</li>
<li><b>Energy Cutoffs:</b> Set the wavefunction cutoff and charge density cutoff in Ry. Charge
density cut off shall be 8 to 12 times of the wavefunction cutoff.</li>
<li><b>Dispersion Corrections:</b> Choose a dispersion correction scheme (e.g., XDM,
grimme-d3).</li>
<li><b>Smearing:</b> Configure occupation types (e.g., smearing, fixed), smearing method (e.g.,
gaussian), and smearing width (degauss). These are primarily needed when system is
metallic.</li>
<li><b>Runs Directory:</b> Choose the output workspace folder in the Advanced Settings option,
where run logs, input files, and state files will be saved.</li>
<li><b>SLURM Settings:</b> Specify required Walltime (e.g., 12:00:00), Number of Core Tasks
(e.g., 8), and Quantum ESPRESSO executable command ('pw.x' is the default and only option in
the current version of the application).</li>
<li><b>GPU usage:</b> To enable GPU usage, tick the 'Use GPU' box. The job will use available
GPU resources.</li>
<li><b>Submit:</b> Click 'Submit Run' to initiate input generation, staging, and SLURM job
submission.</li>
</ul>
<p><b>Runs</b></p>
<ul>
<li>Monitor job status. By default all jobs are listed. User can specify CIF file name, job id,
or user name to filter jobs.</li>
<li>Three clickable buttons at the end of each row are to view job settings, to jump to
results, and to download log files respectively.</li>
</ul>
<p><b>Results</b></p>
<ul>
<li><b>View Results:</b> Select job from the dropdown menu, or specify job id/user name/CIF file
name to view results.</li>
<li><b>Downloadable:</b> Tabulated result as CSV file, output structure files, and QE output
logs.</li>
</ul>
<p><b>Dashboard</b></p>
<ul>
<li>View recent jobs from all users and their status.</li>
</ul>
"""


_DOC_REFERENCES_HTML = """
<p><b>References</b></p>
<ul>
<li><a href="https://doi.org/10.1088/1361-648X/aa8f79" target="_blank">J.Phys.:Condens.Matter 29, 465901 (2017)</a></li>
<li><a href="http://iopscience.iop.org/0953-8984/21/39/395502/" target="_blank">J. Phys. Condens. Matter 21, 395502 (2009)</a></li>
<li>(For GPU enabled Versions) <a href="https://doi.org/10.1063/5.0005082" target="_blank">J. Chem. Phys. 152, 154105 (2020)</a></li>
<li><a href="https://pypi.org/project/cif2cell/" target="_blank">Reference for cif2cell</a></li>
<li><a href="https://pymatgen.org/" target="_blank">Reference for pymatgen</a> (used to determine the number of molecules in the unit cell, Z)</li>
<li><a href="https://www.quantum-espresso.org/Doc/INPUT_PW.html#id51" target="_blank">pw.x input description</a></li>
</ul>
"""


_DOC_REFERENCES_HTML += _contact_html(SITE.get("app", {}).get("contact", _DEFAULT_CONTACTS))


def _doc_accordion(title: str, body_html: str) -> widgets.Accordion:
    content = widgets.HTML(f'<div style="font-size:0.85rem;">{body_html}</div>')
    section = widgets.Accordion(children=[content])
    section.set_title(0, title)
    section.selected_index = None
    section.add_class("advqm-section")
    return section



# © 2017-26. Aganitha Cognitive Solutions Private Limited. Proprietary and Confidential.
