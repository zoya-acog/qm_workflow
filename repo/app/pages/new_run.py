"""New Calculation submission page (single-CIF upload and multi-CIF folder modes)."""

import html
import json
import re
import threading
from datetime import datetime
from pathlib import Path

import ipywidgets as widgets
import ipyvuetify as ipv

from helpers import (
    UPLOAD_DIR,
    _process_single_cif,
    _runs_dir_for_cif,
    _submit_array_batch,
    _validate_gpu_options,
    _validate_vdw_options,
    build_namespace,
    attach_log_handler,
    load_run_state,
    save_uploads,
)
from shared import (
    CALC_TYPE_LABELS, DEFAULT_RUNS_DIR, _Nav, _last_run_error, _list_all_run_rows, _run_log, labeled_field,
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
    sample_cif = Path(__file__).parent / "samples" / "sample.cif"
    sample_selection = {"path": None}
    sample_message = widgets.HTML('<span class="advqm-muted"></span>')
    upload_message = widgets.HTML()
    multi_cif_dir_w = ipv.TextField(
        label="CIF Folder Path",
        v_model="",
        placeholder="Path visible to the workflow server",
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
    force_pp_w = widgets.Checkbox(value=False, description="Force pseudopotential cleanup")
    kpoints_w = ipv.TextField(
        label="k-points Grid (Optional)", v_model="",
        placeholder="e.g. 1 1 1 0 0 0 (leave blank to use separation)",
        outlined=True, dense=True,
    )
    ksep_w = ipv.TextField(
        label="k-point Separation (Preferred)", v_model="0.03",
        placeholder="Used when k-points Grid is blank", outlined=True, dense=True,
    )
    pseudo_dir_w = ipv.TextField(label="Pseudopotential Directory",
                                  v_model="/mnt/own6d/qe_workflow/data/pseudos",
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
            {"text": "RTX 4090", "value": "nvidia_geforce_rtx_4090"},
            {"text": "RTX 5080", "value": "nvidia_geforce_rtx_5080"},
        ],
        v_model="", outlined=True, dense=True, disabled=True,
    )
    gpus_w = ipv.TextField(label="GPUs (One MPI Rank Each)", v_model="1",
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
                                placeholder="e.g. 50 Ry", outlined=True, dense=True)
    ecutrho_w = ipv.TextField(label="Charge Density Cutoff (Ry)", v_model="",
                                placeholder="e.g. 600 Ry (8 to 12 times of Wavefunction cutoff)",
                                outlined=True, dense=True)
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
                                placeholder="e.g. 0.05 Ry", outlined=True, dense=True)

    run_btn = widgets.Button(description="Submit Run", button_style="success", icon="play")
    reset_btn = widgets.Button(description="Cancel", icon="undo")
    sample_btn = widgets.Button(description="Try Sample", icon="refresh")
    run_log = widgets.Output()
    attach_log_handler(run_log)

    def clear_upload() -> None:
        upload_message.value = ""
        # ipywidgets 7 makes FileUpload.value read-only; clear its synced
        # metadata/data and bump the counter to reset the browser file input.
        if hasattr(upload, "metadata") and hasattr(upload, "data"):
            upload.metadata = []
            upload.data = []
            upload._counter += 1
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
            f'<span class="advqm-muted">Using built-in sample: {sample_cif.name}</span>'
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
        pseudo_dir_w.v_model = "/mnt/own6d/qe_workflow/data/pseudos"
        pp_map_w.v_model = ""
        walltime_w.v_model = "12:00:00"
        ntasks_w.v_model = "8"
        mem_w.v_model = "4"
        maxconc_w.v_model = "0"
        qe_cmd_w.v_model = "pw.x"
        use_gpu_w.v_model = False
        gpu_type_w.v_model = ""
        gpus_w.v_model = "1"
        force_pp_w.value = False
        submitted_by_w.v_model = ""
        run_id_w.v_model = ""
        vdw_corr_w.v_model = None
        for field in (input_dft_w, ecutwfc_w, ecutrho_w, xdm_a1_w, xdm_a2_w,
                      occupations_w, smearing_w, degauss_w):
            field.v_model = ""
        run_log.clear_output()
        run_log.append_stdout("Sample values loaded for Single CIF File. Click Submit Run to use the sample CIF.\n")

    sample_btn.on_click(on_sample_clicked)

    def on_run_clicked(b: widgets.Button) -> None:
        if not (submitted_by_w.v_model or "").strip():
            _run_log("User Name is required — enter your user name (e.g. jsmith) before submitting.")
            return
        run_label_val = (run_id_w.v_model or "").strip()
        if run_label_val:
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", run_label_val):
                _run_log("Run ID may only contain letters, digits, '.', '_' and '-' (max 64 characters).")
                return
            try:
                taken = any(
                    r.get("run_label") == run_label_val
                    for r in _list_all_run_rows(Path(runs_dir_w.v_model))
                )
            except Exception:
                taken = False
            if taken:
                _run_log(f"Run ID '{run_label_val}' is already used by another submission; choose a different one.")
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
                    _run_log(f"Upload error: {e}")
                    run_btn.disabled = False
                    return
            if len(staged) != 1:
                _run_log("Single CIF File accepts exactly one .cif file (or choose Try Sample).")
                run_btn.disabled = False
                return
            multi = False
        else:
            # ponytail: accept any server-readable folder path; ceiling: this
            # is not sandboxed to a data root. Upgrade by adding a path allowlist.
            cif_dir_text = (multi_cif_dir_w.v_model or "").strip()
            if not cif_dir_text:
                _run_log("Enter a server-side folder path for Multiple CIF Files.")
                run_btn.disabled = False
                return
            cif_dir = Path(cif_dir_text)
            if not cif_dir.is_dir():
                _run_log(f"Multiple CIF Files folder does not exist or is not a directory: {cif_dir}")
                run_btn.disabled = False
                return
            cif_glob = (cif_glob_w.v_model or "").strip() or "*.cif"
            try:
                staged = sorted(
                    p for p in cif_dir.glob(cif_glob)
                    if p.is_file() and p.suffix.lower() == ".cif"
                )
            except (NotImplementedError, ValueError) as e:
                _run_log(f"Invalid CIF file pattern {cif_glob!r}: {e}")
                run_btn.disabled = False
                return
            if not staged:
                _run_log(f"No CIF files matched {cif_glob!r} in {cif_dir}")
                run_btn.disabled = False
                return
            multi = True
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
            _run_log(f"Invalid numeric parameter: {e}")
            run_btn.disabled = False
            return
        ecutwfc_val = parsed_numeric.get("ecutwfc")
        ecutrho_val = parsed_numeric.get("ecutrho")
        if ecutwfc_val is not None and ecutrho_val is not None:
            if not ecutwfc_val:
                _run_log("Wavefunction cutoff must be nonzero when Charge Density Cutoff is set.")
                run_btn.disabled = False
                return
            ratio = ecutrho_val / ecutwfc_val
            if ratio < 8 or ratio > 12:
                _run_log(
                    "Charge Density Cutoff must be 8 to 12 times of Wavefunction cutoff "
                    f"(got {ecutrho_val:g} / {ecutwfc_val:g} = {ratio:.2f})."
                )
                run_btn.disabled = False
                return
        common = dict(
            calc_type=calc_type_w.v_model or "scf",
            runs_dir=str(base_runs),
            log_name=log_name_w.v_model,
            force_pp_cleanup=force_pp_w.value,
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
            _run_log(vdw_error)
            run_btn.disabled = False
            return

        gpu_error = _validate_gpu_options(build_namespace(**common))
        if gpu_error:
            _run_log(gpu_error)
            run_btn.disabled = False
            return

        submitted_by = submitted_by_w.v_model.strip()
        cif_dir_val = (multi_cif_dir_w.v_model or "").strip() if multi else ""
        log_name = common.get("log_name") or "advQMcalc.log"

        def work() -> None:
            _run_log(f"Starting run for {len(staged)} CIF file(s)")
            submitted = 0
            failed = 0
            use_array = len(staged) > 1
            prepared: list[tuple] = []
            for cif in staged:
                ns = build_namespace(cif=[str(cif)], defer_submit=use_array, **common)
                runs_dir = _runs_dir_for_cif(base_runs, cif, multi)
                try:
                    run_dir = _process_single_cif(cif, runs_dir, ns)
                except Exception as e:
                    failed += 1
                    _run_log(f"Run failed for {cif.name}: {e}")
                    continue
                if run_dir is None:
                    failed += 1
                    _run_log(
                        f"Run failed for {cif.name} before SLURM submission; "
                        f"see {log_name} in its run folder for details."
                    )
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
                    array_id = _submit_array_batch(
                        [rd for _, rd in prepared], base_runs, maxconc_val
                    )
                    _run_log(
                        f"Submitted {len(prepared)} runs as one SLURM array job {array_id}"
                    )
                except Exception as e:
                    _run_log(f"Array submission failed: {e}")

            for cif, run_dir in prepared:
                st = load_run_state(run_dir)
                crystal = (st or {}).get("tasks", {}).get("crystal", {})
                if crystal.get("job_id"):
                    submitted += 1
                    try:
                        state_file = run_dir / "state.json"
                        state = json.loads(state_file.read_text())
                        state["submitted_by"] = submitted_by or None
                        state["run_label"] = run_label_val or None
                        state["cif_dir"] = cif_dir_val or None
                        state_file.write_text(json.dumps(state, indent=2))
                    except Exception:
                        pass
                else:
                    failed += 1
                    err = _last_run_error(run_dir, log_name)
                    if err:
                        _run_log(f"Run failed for {cif.name}: {err}")
                    else:
                        _run_log(
                            f"Run failed for {cif.name} before SLURM submission "
                            f"(status: {crystal.get('status') or 'unknown'})."
                        )
            _run_log(f"Workflow batch finished: {submitted} submitted, {failed} failed.")

        def on_done() -> None:
            run_btn.disabled = False

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def _check() -> None:
            while t.is_alive():
                pass
            on_done()

        threading.Thread(target=_check, daemon=True).start()

    def on_reset_clicked(b: widgets.Button) -> None:
        clear_upload()
        sample_selection["path"] = None
        sample_message.value = ""
        input_tabs.selected_index = 0
        multi_cif_dir_w.v_model = ""
        cif_glob_w.v_model = "*.cif"
        vdw_corr_w.v_model = None
        for field in (input_dft_w, ecutwfc_w, ecutrho_w, xdm_a1_w, xdm_a2_w,
                      occupations_w, smearing_w, degauss_w):
            field.v_model = ""
        run_log.clear_output()
        run_log.append_stdout("Ready.\n")

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
            return labeled_field(w) if isinstance(w, (ipv.TextField, ipv.Select)) else w

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
            widgets.HTML('<div class="advqm-muted">Path must be readable from the workflow server.</div>'),
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
            _ipv_row(submitted_by_w, run_id_w),
        ]
    )
    params_card.add_class("advqm-card")
    params_card.add_class("advqm-card-flat")
    params_section = _collapsible_section("⚙️ Input Settings", params_card, expanded=True)

    slurm_card = widgets.VBox(
        [
            _ipv_row(walltime_w, ntasks_w, qe_cmd_w),
            _ipv_row(mem_w, maxconc_w),
            # "Use GPU" and its two options packed together, not spread over thirds.
            ipv.Row(children=[
                ipv.Col(children=[use_gpu_w], style_="flex:0 0 auto; padding:0 16px 8px 8px;"),
                ipv.Col(children=[labeled_field(gpu_type_w)], style_="flex:0 0 240px; min-width:0; padding:0 8px;"),
                ipv.Col(children=[labeled_field(gpus_w)], style_="flex:0 0 240px; min-width:0; padding:0 8px;"),
            ], style_="margin:0 -8px; align-items:flex-end; flex-wrap:wrap;"),
        ]
    )
    slurm_card.add_class("advqm-card")
    slurm_card.add_class("advqm-card-flat")
    slurm_section = _collapsible_section("🖥️ SLURM Configuration", slurm_card)

    additional_content = widgets.VBox(
        [
            _ipv_row(runs_dir_w),
            _ipv_row(log_name_w),
            _ipv_row(pp_map_w),
            force_pp_w,
        ]
    )
    additional_content.add_class("advqm-card")
    additional_content.add_class("advqm-card-flat")
    additional = _collapsible_section("Additional Settings", additional_content)

    actions = widgets.HBox([sample_btn, run_btn, reset_btn])

    return widgets.VBox(
        [header, upload_section, params_section, additional, slurm_section, actions, run_log]
    )

