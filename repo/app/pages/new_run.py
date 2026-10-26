"""New Calculation submission page (single-CIF upload and multi-CIF folder modes)."""

import html
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import ipywidgets as widgets
import ipyvuetify as ipv

from advQMcalc_test.site_config import SITE
from helpers import (
    UPLOAD_DIR,
    _process_single_cif,
    _runs_dir_for_cif,
    _submit_array_batch,
    _validate_gpu_options,
    _validate_vdw_options,
    build_namespace,
    load_run_state,
    save_uploads,
    prepare_cifs,
    check_cif_folder,
    check_runs_dir,
    check_pseudo_dir,
)
from shared import (
    CALC_TYPE_LABELS, DEFAULT_RUNS_DIR, _Nav, _last_run_error, _list_all_run_rows, labeled_field,
    remember_runs_dir,
)


# QE default XDM damping parameters (xdm_a1, xdm_a2).
XDM_DEFAULT_A1 = "0.6836"
XDM_DEFAULT_A2 = "1.5045"


def _build_new_run_page(nav: _Nav):
    upload = widgets.FileUpload(
        accept=".cif", multiple=False, description="Upload", icon="upload",
        button_style="primary",
    )
    sample_cif = Path(__file__).resolve().parent.parent / "samples" / "benzene.cif"
    sample_selection = {"path": None}
    sample_message = widgets.HTML('<span class="advqm-muted"></span>')
    upload_message = widgets.HTML()
    multi_cif_dir_w = ipv.TextField(
        label="CIF Folder Path",
        v_model="",
        placeholder="e.g. /path/to/cif_folder",
        outlined=True,
        dense=True,
    )
    cif_glob_w = ipv.TextField(
        label="CIF Filename Pattern", v_model="*.cif", outlined=True, dense=True
    )
    input_tabs = widgets.Tab()
    # These use ipyvuetify (not plain ipywidgets) — its Material text fields
    # handle box-sizing/width correctly inside percentage-width flex columns,
    # which plain ipywidgets <input> elements were not doing reliably (fields
    # were getting cut off on the right / causing horizontal scroll).
    calc_type_w = ipv.Select(label="Calculation Type", items=[{"text": f"{t} ({k})", "value": k} for k, t in CALC_TYPE_LABELS.items()],
                              v_model="scf", outlined=True, dense=True)
    runs_dir_w = ipv.TextField(
        label="Runs Directory", v_model=DEFAULT_RUNS_DIR, outlined=True, dense=True
    )
    log_name_w = ipv.TextField(
        label="Log File Name", v_model="advQMcalc.log", outlined=True, dense=True
    )
    kpoints_w = ipv.TextField(
        label="k-points Grid (Optional)", v_model="",
        placeholder="e.g. 1 1 1 0 0 0 (leave blank to use separation)",
        outlined=True, dense=True,
    )
    ksep_w = ipv.TextField(
        label="k-point Separation (1/Å) (Preferred)", v_model="0.03",
        placeholder="Used when k-points Grid is blank", outlined=True, dense=True,
    )
    pseudo_dir_w = ipv.TextField(label="Pseudopotential Directory",
                                  v_model=SITE["app"]["pseudo_dir"],
                                  placeholder="/path/to/pseudo", outlined=True, dense=True)
    pp_map_w = ipv.TextField(
        label="PP Map (Optional)", v_model="", placeholder="/path/to/pp_map.json",
        outlined=True, dense=True,
    )
    walltime_w = ipv.TextField(label="Walltime", v_model="12:00:00", outlined=True, dense=True)
    ntasks_w = ipv.TextField(label="Tasks", v_model="8", outlined=True, dense=True)
    maxconc_w = ipv.TextField(label="Max Concurrent Jobs (Multi-CIF)", v_model="0", outlined=True,
                              dense=True, placeholder="0 = no limit")
    mem_w = ipv.TextField(label="Memory Per CPU (GB)", v_model="4", outlined=True, dense=True,
                          placeholder="e.g. 4 (use 8+ for >100 atoms)")
    qe_cmd_w = ipv.TextField(label="QE Command", v_model="pw.x", outlined=True, dense=True)
    use_gpu_w = ipv.Checkbox(label="Use GPU", v_model=False, dense=True, hide_details=True,
                             class_="advqm-gpu-check")
    gpu_type_w = ipv.Select(
        label="GPU Type",
        items=[
            {"text": "Any", "value": ""},
            *SITE["app"]["gpu_types"],
        ],
        v_model="", outlined=True, dense=True, disabled=True,
    )
    gpus_w = ipv.TextField(label="No. of GPUs", v_model="1",
                           outlined=True, dense=True, disabled=True)

    def on_gpu_toggle(change) -> None:
        # Tasks is fixed to the GPU count (one MPI rank per GPU) in GPU mode.
        on = bool(change["new"])
        gpu_type_w.disabled = not on
        gpus_w.disabled = not on
        ntasks_w.disabled = on

    use_gpu_w.observe(on_gpu_toggle, names="v_model")
    submitted_by_w = ipv.TextField(label="User Name *", v_model="",
                                     placeholder="e.g. jsmith", outlined=True, dense=True)
    run_id_w = ipv.TextField(label="Run ID (Optional)", v_model="",
                             placeholder="e.g. benzene-test-1", outlined=True, dense=True)
    input_dft_w = ipv.TextField(label="DFT Functional", v_model="",
                                    placeholder="e.g. PBE (leave blank to automatically read from pseudopotential file)",
                                    outlined=True, dense=True)
    vdw_corr_w = ipv.Select(
        label="vdW Correction",
        items=["Grimme-D2", "DFT-D", "Grimme-D3", "DFT-D3", "TS", "MBD", "XDM"],
        v_model=None,
        outlined=True,
        dense=True,
        clearable=True,
    )
    ecutwfc_w = ipv.TextField(label="Wavefunction Cutoff (Ry)", v_model="",
                                placeholder="e.g. 50", outlined=True, dense=True)
    ecutrho_w = ipv.TextField(label="Charge Density Cutoff (Ry)", v_model="",
                                placeholder="e.g. 600", outlined=True, dense=True)
    # Hover text for the ⓘ icon next to a field's caption (keyed by widget id).
    field_info = {
        id(ecutwfc_w): "Plane-wave cutoff for wavefunctions (ecutwfc), e.g. 50 Ry.\n"
                       "Leave blank to use the minimum suggested by the pseudopotential files.",
        id(ecutrho_w): "e.g. 600 (8 to 12 times the Wavefunction Cutoff)",
    }
    xdm_a1_w = ipv.TextField(label="XDM a1 (XDM Only)", v_model="",
                             placeholder="e.g. 0.6836", outlined=True, dense=True, disabled=True)
    xdm_a2_w = ipv.TextField(label="XDM a2 (XDM Only)", v_model="",
                             placeholder="e.g. 1.5045", outlined=True, dense=True, disabled=True)
    def on_vdw_change(change) -> None:
        # XDM a1/a2 only apply to the XDM correction; grey them out (and drop
        # any typed value) otherwise.
        is_xdm = (change["new"] or "").strip().lower() == "xdm"
        for w, default in ((xdm_a1_w, XDM_DEFAULT_A1), (xdm_a2_w, XDM_DEFAULT_A2)):
            w.disabled = not is_xdm
            if not is_xdm:
                w.v_model = ""
            elif not (w.v_model or "").strip():
                # Autofill with QE's default XDM parameters (the backend leaves
                # them unset, so QE applies these same values); still editable.
                w.v_model = default

    vdw_corr_w.observe(on_vdw_change, names="v_model")
    occupations_w = ipv.TextField(label="Occupations", v_model="", placeholder="e.g. smearing", outlined=True, dense=True)
    smearing_w = ipv.TextField(label="Smearing", v_model="", placeholder="e.g. gaussian", outlined=True, dense=True)
    degauss_w = ipv.TextField(label="Degauss (Ry)", v_model="",
                                placeholder="e.g. 0.05", outlined=True, dense=True)
    field_info.update({
        id(xdm_a1_w): "XDM damping parameter a1 (dimensionless), used only with the XDM vdW correction.\n"
                      "Autofilled with QE's default 0.6836; the value should match the DFT functional.",
        id(xdm_a2_w): "XDM damping parameter a2 in Å, used only with the XDM vdW correction.\n"
                      "Autofilled with QE's default 1.5045; the value should match the DFT functional.",
        id(occupations_w): "How electronic states are filled, e.g. 'fixed' for insulators or 'smearing' for metals.\n"
                           "Leave blank to use QE's default ('fixed').",
        id(smearing_w): "Smearing function, e.g. 'gaussian', 'mv' or 'mp'.\n"
                        "Used only when Occupations is 'smearing'; QE's default is 'gaussian'.",
        id(degauss_w): "Smearing width in Ry, e.g. 0.01 to 0.05.\n"
                       "Used only when Occupations is 'smearing'.",
    })

    run_btn = widgets.Button(description="Submit Run", button_style="success", icon="play")
    reset_btn = widgets.Button(description="Reset", icon="undo")
    sample_btn = widgets.Button(description="Try Sample", icon="refresh")
    # Messages for the current submission only: form errors, then the outcome.
    # Each run's detailed log is on the Runs page (⚙ → Monitor).
    run_msg = widgets.HTML()
    run_msg.add_class("advqm-submit-msg")
    msg_lines: list[str] = []
    go_runs_btn = widgets.Button(description="Go to Runs", icon="list",
                                 layout=widgets.Layout(display="none"))
    go_runs_btn.on_click(lambda _: nav.show("runs"))

    def _say(msg: str, css: str = "") -> None:
        text = html.escape(msg, quote=False)
        msg_lines.append(f'<div class="{css}">{text}</div>' if css else f"<div>{text}</div>")
        run_msg.value = "".join(msg_lines)

    def _clear_msgs() -> None:
        msg_lines.clear()
        run_msg.value = ""
        go_runs_btn.layout.display = "none"

    def _first_line(err) -> str:
        line = next((ln.strip() for ln in str(err).splitlines() if ln.strip()), str(err))
        return line if len(line) <= 200 else line[:197] + "..."

    def clear_upload() -> None:
        upload_message.value = ""
        # ipywidgets 7 makes FileUpload.value read-only; clear its synced
        # metadata/data. The button shows _counter as "Upload (n)", so set it
        # back to 0 (incrementing it made one sample look like several files).
        if hasattr(upload, "metadata") and hasattr(upload, "data"):
            upload.metadata = []
            upload.data = []
            upload._counter = 0
            upload.description = "Upload"  # the counter change set it to "Upload (0)"
        else:
            upload.value = ()

    def on_upload_change(change) -> None:
        new = change.get("new")
        if new:
            sample_selection["path"] = None
            sample_message.value = ""
            # ipywidgets 8: tuple of dicts; ipywidgets 7: {filename: meta}
            first = new[0] if isinstance(new, (tuple, list)) else next(iter(new.values()), {})
            name = first.get("name") if isinstance(first, dict) else None
            if not name and isinstance(new, dict):
                name = next(iter(new), None)
            upload_message.value = (
                '<span style="color:#2e7d32; font-weight:600;">&#10004; '
                f'{html.escape(str(name or "file"))}</span>'
            )
        else:
            upload_message.value = ""

    upload.observe(on_upload_change, names="value")

    def on_sample_clicked(b: widgets.Button) -> None:
        input_tabs.selected_index = 0
        clear_upload()
        sample_selection["path"] = sample_cif if sample_cif.is_file() else None
        sample_message.value = (
            # Shown like an uploaded file: green tick and the file name.
            '<span style="color:#2e7d32; font-weight:600;">&#10004; '
            f'{html.escape(sample_cif.name)}</span> <span class="advqm-muted">(built-in sample)</span>'
            if sample_selection["path"] else
            '<span class="advqm-badge advqm-badge-danger">Sample CIF is missing</span>'
        )
        multi_cif_dir_w.v_model = ""
        cif_glob_w.v_model = "*.cif"
        calc_type_w.v_model = "scf"
        runs_dir_w.v_model = DEFAULT_RUNS_DIR
        log_name_w.v_model = "advQMcalc.log"
        kpoints_w.v_model = ""
        ksep_w.v_model = "0.03"
        pseudo_dir_w.v_model = SITE["app"]["pseudo_dir"]
        pp_map_w.v_model = ""
        walltime_w.v_model = "12:00:00"
        ntasks_w.v_model = "8"
        mem_w.v_model = "4"
        maxconc_w.v_model = "0"
        qe_cmd_w.v_model = "pw.x"
        use_gpu_w.v_model = False
        gpu_type_w.v_model = ""
        gpus_w.v_model = "1"
        # User Name is required: keep a name already typed, else a sample label,
        # so Submit Run works straight away. Run ID is optional and left empty.
        if not (submitted_by_w.v_model or "").strip():
            submitted_by_w.v_model = "sample-user"
        run_id_w.v_model = ""
        vdw_corr_w.v_model = "Grimme-D3"
        for field in (input_dft_w, ecutwfc_w, ecutrho_w, xdm_a1_w, xdm_a2_w,
                      occupations_w, smearing_w, degauss_w):
            field.v_model = ""
        _clear_msgs()
        _say("Sample values loaded for Single CIF File. Click Submit Run to use the sample CIF.")

    sample_btn.on_click(on_sample_clicked)

    def on_run_clicked(b: widgets.Button) -> None:
        _clear_msgs()
        if not (submitted_by_w.v_model or "").strip():
            _say("User Name is required — enter your user name (e.g. jsmith) before submitting.")
            return
        run_label_val = (run_id_w.v_model or "").strip()
        if run_label_val:
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", run_label_val):
                _say("Run ID may only contain letters, digits, '.', '_' and '-' (max 64 characters).")
                return
            try:
                taken = any(
                    r.get("run_label") == run_label_val
                    for r in _list_all_run_rows(Path(runs_dir_w.v_model))
                )
            except Exception:
                taken = False
            if taken:
                _say(f"Run ID '{run_label_val}' is already used by another submission; choose a different one.")
                return
        run_btn.disabled = True
        base_runs = Path(runs_dir_w.v_model)
        remember_runs_dir(base_runs)  # so the Dashboard/Results also read this folder
        if input_tabs.selected_index == 0:
            sample_path = sample_selection["path"]
            if sample_path is not None:
                staged = [sample_path]
            else:
                try:
                    staged = save_uploads(upload, UPLOAD_DIR)
                except Exception as e:
                    _say(f"Upload error: {e}")
                    run_btn.disabled = False
                    return
            if len(staged) != 1:
                _say("Single CIF File accepts exactly one .cif file (or choose Try Sample).")
                run_btn.disabled = False
                return
            multi = False
        else:
            # ponytail: accept any server-readable folder path; ceiling: this
            # is not sandboxed to a data root. Upgrade by adding a path allowlist.
            cif_dir_text = (multi_cif_dir_w.v_model or "").strip()
            cif_dir = Path(cif_dir_text)
            cif_glob = (cif_glob_w.v_model or "").strip() or "*.cif"
            staged, path_error = check_cif_folder(cif_dir_text, cif_glob)
            if path_error:
                _say(path_error, "err")
                run_btn.disabled = False
                return
            multi = True
        for path_error in (check_runs_dir(base_runs), check_pseudo_dir(pseudo_dir_w.v_model)):
            if path_error:
                _say(path_error, "err")
                run_btn.disabled = False
                return
        # Spaces/special characters in file names and space-group names cif2cell
        # cannot read are fixed in copies; the user's files are not changed.
        try:
            staged, prep_notes = prepare_cifs(staged, UPLOAD_DIR)
        except Exception as e:
            _say(f"Could not prepare the CIF file(s): {e}")
            run_btn.disabled = False
            return
        for note in prep_notes:
            _say(note)
        try:
            ntasks_val = int(ntasks_w.v_model)
            mem_gb = float(mem_w.v_model)
            maxconc_val = int(maxconc_w.v_model or 0)
            if maxconc_val < 0:
                raise ValueError("Max concurrent jobs cannot be negative")
            if mem_gb <= 0:
                raise ValueError("Memory per CPU must be positive")
            gpus_val = int(gpus_w.v_model or 1)
            kpoints_value = (kpoints_w.v_model or "").strip()
            ksep_text = (ksep_w.v_model or "").strip()
            if kpoints_value:
                # Explicit grid takes precedence; separation is not used.
                ksep_val = 0.03
            elif not ksep_text:
                raise ValueError("Enter a k-points Grid or k-point Separation")
            else:
                ksep_val = float(ksep_text)
            numeric_params = {
                "ecutwfc": ecutwfc_w.v_model,
                "ecutrho": ecutrho_w.v_model,
                "xdm_a1": xdm_a1_w.v_model,
                "xdm_a2": xdm_a2_w.v_model,
                "degauss": degauss_w.v_model,
            }
            parsed_numeric = {
                key: (float(value) if (value or "").strip() else None)
                for key, value in numeric_params.items()
            }
        except (TypeError, ValueError) as e:
            _say(f"Invalid numeric parameter: {e}")
            run_btn.disabled = False
            return
        ecutwfc_val = parsed_numeric.get("ecutwfc")
        ecutrho_val = parsed_numeric.get("ecutrho")
        if ecutwfc_val is not None and ecutrho_val is not None:
            if not ecutwfc_val:
                _say("Wavefunction cutoff must be nonzero when Charge Density Cutoff is set.")
                run_btn.disabled = False
                return
            ratio = ecutrho_val / ecutwfc_val
            if ratio < 8 or ratio > 12:
                _say(
                    "Charge Density Cutoff must be 8 to 12 times of Wavefunction cutoff "
                    f"(got {ecutrho_val:g} / {ecutwfc_val:g} = {ratio:.2f})."
                )
                run_btn.disabled = False
                return
        common = dict(
            calc_type=calc_type_w.v_model or "scf",
            runs_dir=str(base_runs),
            log_name=log_name_w.v_model,
            force_pp_cleanup=False,  # every new run does the cleanup anyway
            pseudo_dir=(pseudo_dir_w.v_model or "").strip() or None,
            pp_map=(pp_map_w.v_model or "").strip() or None,
            slurm_walltime=walltime_w.v_model,
            slurm_ntasks=ntasks_val,
            slurm_mem_per_cpu=f"{mem_gb:g}G",
            qe_command=qe_cmd_w.v_model,
            gpu=bool(use_gpu_w.v_model),
            gpu_type=(gpu_type_w.v_model or None),
            gpus=gpus_val,
            kpoints=kpoints_value or None,
            kpoint_separation=ksep_val,
            input_dft=(input_dft_w.v_model or "").strip() or None,
            vdw_corr=(vdw_corr_w.v_model or "").strip() or None,
            occupations=(occupations_w.v_model or "").strip() or None,
            smearing=(smearing_w.v_model or "").strip() or None,
            **parsed_numeric,
        )

        vdw_error = _validate_vdw_options(build_namespace(**common))
        if vdw_error:
            _say(vdw_error)
            run_btn.disabled = False
            return

        gpu_error = _validate_gpu_options(build_namespace(**common))
        if gpu_error:
            _say(gpu_error)
            run_btn.disabled = False
            return

        submitted_by = submitted_by_w.v_model.strip()
        cif_dir_val = (multi_cif_dir_w.v_model or "").strip() if multi else ""
        log_name = common.get("log_name") or "advQMcalc.log"

        def stamp_run(run_dir: Path) -> None:
            """Record who submitted the run (and its Run ID / folder) in its state,
            for every run created, including runs that fail during setup."""
            try:
                state_file = run_dir / "state.json"
                state = json.loads(state_file.read_text())
                state["submitted_by"] = submitted_by or None
                state["run_label"] = run_label_val or None
                state["cif_dir"] = cif_dir_val or None
                state_file.write_text(json.dumps(state, indent=2))
            except Exception:
                pass

        def failed_run_dir(runs_dir: Path, cif: Path, since: float) -> Path | None:
            """The run folder a failed setup created for this CIF (newest one)."""
            found = []
            for sf in Path(runs_dir).glob("*/state.json"):
                try:
                    if sf.stat().st_mtime >= since and json.loads(sf.read_text()).get("cif") == str(cif):
                        found.append(sf.parent)
                except Exception:
                    continue
            return max(found, key=lambda d: d.stat().st_mtime) if found else None

        def work() -> None:
            _say(f"Submitting {len(staged)} CIF file(s)…")
            failures: list[str] = []
            job_ids: list[str] = []
            use_array = len(staged) > 1
            prepared: list[tuple] = []
            for cif in staged:
                ns = build_namespace(cif=[str(cif)], defer_submit=use_array, **common)
                runs_dir = _runs_dir_for_cif(base_runs, cif, multi)
                started = time.time() - 1
                try:
                    run_dir = _process_single_cif(cif, runs_dir, ns)
                except Exception as e:
                    rd = failed_run_dir(runs_dir, cif, started)
                    if rd:
                        stamp_run(rd)
                    failures.append(f"{cif.name}: {_first_line(e)}")
                    continue
                if run_dir is None:
                    rd = failed_run_dir(runs_dir, cif, started)
                    if rd:
                        stamp_run(rd)
                    err = _last_run_error(rd, log_name) if rd else None
                    failures.append(f"{cif.name}: {_first_line(err) if err else 'failed during setup'}")
                    continue
                prepared.append((cif, Path(run_dir)))

            if use_array and prepared:
                # Link the runs of this submission up front, so they stay grouped
                # in the tables even if the array submission below fails.
                batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                for _cif, rd in prepared:
                    try:
                        sf = rd / "state.json"
                        st_ = json.loads(sf.read_text())
                        st_["batch_id"] = batch_id
                        sf.write_text(json.dumps(st_, indent=2))
                    except Exception:
                        pass
                try:
                    job_ids.append(str(_submit_array_batch(
                        [rd for _, rd in prepared], base_runs, maxconc_val
                    )))
                except Exception as e:
                    failures.append(f"SLURM array submission: {_first_line(e)}")

            submitted = 0
            for cif, run_dir in prepared:
                st = load_run_state(run_dir)
                crystal = (st or {}).get("tasks", {}).get("crystal", {})
                stamp_run(run_dir)
                if crystal.get("job_id"):
                    submitted += 1
                    if not use_array:
                        job_ids.append(str(crystal["job_id"]))
                else:
                    err = _last_run_error(run_dir, log_name)
                    failures.append(f"{cif.name}: {_first_line(err) if err else 'not submitted to SLURM'}")

            if submitted:
                _say(f"✔ Submitted {submitted} run(s) as SLURM job {', '.join(job_ids)}.", "ok")
            for f in failures:
                _say(f"✖ {f}", "err")
            if not submitted:
                _say("Nothing was submitted.", "err")
            _say("Track your runs on the Runs page: click the ⚙ icon on a run to open "
                 "Monitor, which shows its status and log.")
            go_runs_btn.layout.display = ""

        def on_done() -> None:
            run_btn.disabled = False

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def _check() -> None:
            t.join()
            on_done()

        threading.Thread(target=_check, daemon=True).start()

    # Every input with the value the form opened with, for Reset.
    form_fields = [
        multi_cif_dir_w, cif_glob_w, calc_type_w, runs_dir_w, log_name_w, kpoints_w,
        ksep_w, pseudo_dir_w, pp_map_w, walltime_w, ntasks_w, mem_w, maxconc_w,
        qe_cmd_w, use_gpu_w, gpu_type_w, gpus_w, submitted_by_w, run_id_w,
        vdw_corr_w, input_dft_w, ecutwfc_w, ecutrho_w, xdm_a1_w, xdm_a2_w,
        occupations_w, smearing_w, degauss_w,
    ]
    initial_values = [(w, w.v_model) for w in form_fields]

    def on_reset_clicked(b: widgets.Button) -> None:
        # Back to the form as first opened: no CIF, every field at its default.
        clear_upload()
        sample_selection["path"] = None
        sample_message.value = ""
        input_tabs.selected_index = 0
        for w, value in initial_values:
            w.v_model = value
        _clear_msgs()

    run_btn.on_click(on_run_clicked)
    reset_btn.on_click(on_reset_clicked)

    header = widgets.HTML(
        '<div class="advqm-page-header">'
        '<div><div class="advqm-page-title">New Calculation</div>'
        '<div class="advqm-page-sub">Configure and submit a Quantum ESPRESSO workflow</div></div>'
        "</div>"
    )

    def _ipv_row(*widgets_):
        # Field names sit in a plain label above each input (no floating labels
        # on the border), so the column needs no extra top padding for them.
        def _prep(w):
            if isinstance(w, (ipv.TextField, ipv.Select)):
                return labeled_field(w, info=field_info.get(id(w)))
            return w

        return ipv.Row(children=[
            ipv.Col(children=[_prep(w)], style_="flex:1; min-width:0; padding:0 8px;") for w in widgets_
        ], style_="margin:0 -8px;")

    def _collapsible_section(title: str, content, expanded: bool = False):
        section = widgets.Accordion(children=[content])
        section.set_title(0, title)
        section.selected_index = 0 if expanded else None
        section.add_class("advqm-section")
        return section

    single_file_panel = widgets.VBox([
        widgets.HTML('<div class="advqm-muted">Upload a single .cif structure from your computer.</div>'),
        upload,
        upload_message,
        sample_message,
    ])
    single_file_panel.add_class("advqm-file-panel")
    input_tabs.children = [
        single_file_panel,
        widgets.VBox([
            widgets.HTML('<div class="advqm-muted">Absolute path to the CIF files in the server (Note: ensure the path is accessible to the application).</div>'),
            _ipv_row(multi_cif_dir_w, cif_glob_w),
        ]),
    ]
    input_tabs.set_title(0, "Single CIF File")
    input_tabs.set_title(1, "Multiple CIF Files")
    upload_card = widgets.VBox([input_tabs])
    upload_card.add_class("advqm-card")
    upload_card.add_class("advqm-card-flat")
    upload_section = _collapsible_section("📄 Crystal Structure Files", upload_card, expanded=True)

    params_card = widgets.VBox(
        [
            _ipv_row(calc_type_w, pseudo_dir_w),
            _ipv_row(kpoints_w, ksep_w),
            _ipv_row(input_dft_w, vdw_corr_w),
            _ipv_row(xdm_a1_w, xdm_a2_w, ecutwfc_w),
            _ipv_row(ecutrho_w, occupations_w, smearing_w),
            _ipv_row(degauss_w),
        ]
    )
    params_card.add_class("advqm-card")
    params_card.add_class("advqm-card-flat")
    params_section = _collapsible_section("⚙️ QE Settings", params_card, expanded=True)

    slurm_card = widgets.VBox(
        [
            _ipv_row(walltime_w, ntasks_w, qe_cmd_w),
            _ipv_row(mem_w, maxconc_w),
            # "Use GPU" on the same line as its two options. An empty caption above
            # the checkbox lines it up with the input boxes, not their labels.
            ipv.Row(children=[
                ipv.Col(children=[
                    ipv.Html(tag="div", children=[" "],
                             style_="font-size:0.86rem; margin:8px 0 2px 0;"),
                    ipv.Html(tag="div", children=[use_gpu_w],
                             style_="height:40px; display:flex; align-items:center;"),
                # Fixed width: the Vuetify checkbox otherwise stretches to the
                # full row and pushes the two GPU fields out of view.
                ], style_="flex:0 0 150px; max-width:150px; padding:0 16px 0 8px;"),
                ipv.Col(children=[labeled_field(gpu_type_w)], style_="flex:0 0 240px; min-width:0; padding:0 8px;"),
                ipv.Col(children=[labeled_field(gpus_w)], style_="flex:0 0 240px; min-width:0; padding:0 8px;"),
            ], style_="margin:0 -8px; align-items:flex-start; flex-wrap:nowrap;"),
        ]
    )
    slurm_card.add_class("advqm-card")
    slurm_card.add_class("advqm-card-flat")
    slurm_section = _collapsible_section("🖥️ SLURM Configuration", slurm_card)

    additional_content = widgets.VBox(
        [
            _ipv_row(submitted_by_w, run_id_w),
            _ipv_row(runs_dir_w),
            _ipv_row(log_name_w),
            _ipv_row(pp_map_w),
        ]
    )
    additional_content.add_class("advqm-card")
    additional_content.add_class("advqm-card-flat")
    # Open by default: it holds the required User Name.
    additional = _collapsible_section("🛠️ Additional Settings", additional_content, expanded=True)

    actions = widgets.HBox([sample_btn, run_btn, reset_btn])

    return widgets.VBox(
        [header, upload_section, params_section, additional, slurm_section, actions, run_msg, go_runs_btn]
    )

