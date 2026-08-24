from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable

from .analysis import (
    ADVANCED_GRID_BEATS,
    PITCH_NAMES,
    SIMPLE_GRID_BEATS,
    annotate_event_confidence,
    analyze_audio,
    build_transcription_evidence,
    clean_performance_midi,
    read_midi_events,
    simplify_events,
    summarize_event_confidence,
    write_events_midi,
)
from .export import export_musicxml, normalize_events_to_written_range, render_pdf
from .llm_review import ReviewResult, apply_safe_corrections, review
from .separation import (
    LalalSeparatorProvider,
    StemCandidate,
    UVRSeparatorProvider,
    candidate_quality,
    compare_event_sets,
)
from .settings import settings


Progress = Callable[[str, int, str], None]
logger = logging.getLogger(__name__)


class JobCancelled(RuntimeError):
    pass


def _ensure_active(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check and cancel_check():
        raise JobCancelled("Processing cancelled")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_wind_model(path: Path, expected_sha256: str) -> str:
    if not path.is_file():
        raise RuntimeError(
            f"Required wind model is missing: {path}. "
            "Run: bash scripts/download-wind-model.sh"
        )
    actual = _sha256(path)
    if actual.lower() != expected_sha256.lower():
        raise RuntimeError(
            f"Wind model checksum is wrong: {path}. Expected {expected_sha256}, got {actual}. "
            "Delete that file and run: bash scripts/download-wind-model.sh"
        )
    return actual


def _uvr_output_filename(source_name: str | None, stem_label: str) -> str:
    """Build an output name from the current upload and configured stem label."""
    source_stem = Path(source_name or "recording").stem
    safe_stem = re.sub(r"[^\w .'-]+", "-", source_stem, flags=re.UNICODE).strip(" .-")
    safe_label = re.sub(r"[^\w .'-]+", "-", stem_label, flags=re.UNICODE).strip(" .-")
    return f"1_{safe_stem or 'recording'}_({safe_label or 'Stem'}).wav"


def _flatten_model_names(value) -> list[str]:
    names: list[str] = []
    if isinstance(value, str):
        if value.lower().endswith((".pth", ".onnx", ".ckpt", ".yaml")):
            names.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            names.extend(_flatten_model_names(key))
            names.extend(_flatten_model_names(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            names.extend(_flatten_model_names(item))
    return list(dict.fromkeys(names))


def _resolve_wind_model(separator) -> str:
    requested = settings.uvr_model_name
    try:
        catalogue = separator.list_supported_model_files()
        names = _flatten_model_names(catalogue)
    except Exception:
        return requested
    if requested in names:
        return requested
    raise RuntimeError(
        f"The required UVR model is not available: {requested}. "
        "SaxScribe will not silently substitute a different separator."
    )


def _pick_target_stem_output(paths: list[str], output_dir: Path, target_stem: str) -> Path:
    resolved = [Path(item) if Path(item).is_absolute() else output_dir / item for item in paths]
    resolved = [item for item in resolved if item.exists()]
    if not resolved:
        raise RuntimeError("The UVR model returned no output files.")

    normalized_target = " ".join(target_stem.lower().replace("_", " ").replace("-", " ").split())

    def normalized_name(path: Path) -> str:
        return " ".join(path.stem.lower().replace("_", " ").replace("-", " ").replace("(", " ").replace(")", " ").split())

    positive = []
    for path in resolved:
        name = normalized_name(path)
        contains_target = normalized_target in name
        is_negative = f"no {normalized_target}" in name or f"without {normalized_target}" in name
        if contains_target and not is_negative:
            positive.append(path)

    if len(positive) == 1:
        return positive[0]
    returned = ", ".join(path.name for path in resolved)
    raise RuntimeError(
        f"UVR did not return one unambiguous '{target_stem}' stem. Returned: {returned}. "
        "SaxScribe refuses to send the complementary 'No Woodwinds' stem to transcription."
    )


def _pick_complementary_stem_output(paths: list[str], output_dir: Path, target_stem: str) -> Path:
    resolved = [Path(item) if Path(item).is_absolute() else output_dir / item for item in paths]
    resolved = [item for item in resolved if item.exists()]
    normalized_target = " ".join(target_stem.lower().replace("_", " ").replace("-", " ").split())

    def normalized_name(path: Path) -> str:
        return " ".join(path.stem.lower().replace("_", " ").replace("-", " ").replace("(", " ").replace(")", " ").split())

    negative = [path for path in resolved if f"no {normalized_target}" in normalized_name(path)]
    if len(negative) == 1:
        return negative[0]
    returned = ", ".join(path.name for path in resolved)
    raise RuntimeError(f"UVR did not return one unambiguous 'No {target_stem}' reference stem. Returned: {returned}.")


def _separate_wind_in_process(
    source: Path,
    output_dir: Path,
    separator_factory=None,
    source_display_name: str | None = None,
) -> Path:
    model_path = Path(settings.uvr_model_dir) / settings.uvr_model_name
    model_sha256 = None
    if separator_factory is None:
        model_sha256 = _verify_wind_model(model_path, settings.uvr_model_sha256)
        from audio_separator.separator import Separator
        separator_factory = Separator

    kwargs = {
        "output_dir": str(output_dir),
        "output_format": settings.uvr_output_format,
        "sample_rate": 44100,
        "use_soundfile": True,
        # Match UVR's "Wind Inst Only" checkbox: write only the secondary
        # Woodwinds stem, never the complementary No Woodwinds array.
        "output_single_stem": settings.uvr_target_stem,
        # UVR writes PCM peaks up to full scale when normalization is disabled.
        "normalization_threshold": 1.0,
        "vr_params": {
            "batch_size": 1,
            "window_size": settings.uvr_vr_window_size,
            "aggression": settings.uvr_vr_aggression,
            "enable_tta": False,
            # Reproduce the high-frequency reconstruction present in the
            # user's working UVR GUI output.
            "high_end_process": settings.uvr_vr_high_end_process,
            "enable_post_process": False,
        },
    }
    kwargs["model_file_dir"] = settings.uvr_model_dir
    separator = separator_factory(**kwargs)
    if settings.uvr_force_cpu:
        cpu_device = getattr(separator, "torch_device_cpu", None)
        if cpu_device is not None:
            separator.torch_device = cpu_device
            separator.torch_device_mps = None
            separator.onnx_execution_provider = ["CPUExecutionProvider"]
    model_name = _resolve_wind_model(separator)
    separator.load_model(model_filename=model_name)
    model_instance = getattr(separator, "model_instance", None)
    if settings.uvr_force_cpu and model_instance is not None:
        cpu_device = getattr(model_instance, "torch_device_cpu", None)
        if cpu_device is not None:
            model_instance.torch_device = cpu_device
            model_instance.torch_device_mps = None
            model_instance.onnx_execution_provider = ["CPUExecutionProvider"]
    model_params = getattr(model_instance, "model_params", None)
    parameter_data = getattr(model_params, "param", {})
    for band in parameter_data.get("band", {}).values():
        if isinstance(band, dict):
            band["res_type"] = settings.uvr_resampler
    loaded_primary = getattr(model_instance, "primary_stem_name", None)
    loaded_secondary = getattr(model_instance, "secondary_stem_name", None)
    loaded_stems = [item for item in (loaded_primary, loaded_secondary) if item]
    if loaded_stems and settings.uvr_target_stem.lower() not in {item.lower() for item in loaded_stems}:
        raise RuntimeError(
            f"The loaded UVR model exposes {loaded_stems}, not the required "
            f"'{settings.uvr_target_stem}' stem."
        )
    effective = {
        "primary_stem": loaded_primary,
        "secondary_stem": loaded_secondary,
        "device": str(getattr(model_instance, "torch_device", getattr(separator, "torch_device", "unknown"))),
        "window_size": getattr(model_instance, "window_size", None),
        "batch_size": getattr(model_instance, "batch_size", None),
        "aggression": getattr(model_instance, "aggression", None),
        "high_end_process": getattr(model_instance, "high_end_process", None),
        "resampler": settings.uvr_resampler,
    }
    logger.info("SaxScribe effective UVR settings: %s", json.dumps(effective, sort_keys=True))

    uvr_output_filename = _uvr_output_filename(
        source_display_name or source.name,
        settings.uvr_output_label,
    )
    uvr_output_basename = uvr_output_filename.removesuffix(".wav")
    output_names = {
        settings.uvr_target_stem: uvr_output_basename,
        f"No {settings.uvr_target_stem}": f"no-wind-reference-(No {settings.uvr_target_stem})",
    }
    outputs = separator.separate(str(source), custom_output_names=output_names)
    returned_paths = [Path(item) if Path(item).is_absolute() else output_dir / item for item in outputs]
    expected_target = output_dir / uvr_output_filename
    exact_matches = [path for path in returned_paths if path.exists() and path.resolve() == expected_target.resolve()]
    selected = exact_matches[0] if len(exact_matches) == 1 else _pick_target_stem_output(
        list(outputs), output_dir, settings.uvr_target_stem
    )
    target = selected
    try:
        separator_version = version("audio-separator")
    except PackageNotFoundError:
        separator_version = "unknown"
    manifest = {
        "engine": "audio-separator",
        "engine_version": separator_version,
        "model": model_name,
        "model_path": str(model_path),
        "model_sha256": model_sha256 or "test-double-not-verified",
        "loaded_primary_stem": loaded_primary,
        "loaded_secondary_stem": loaded_secondary,
        "requested_transcription_stem": settings.uvr_target_stem,
        "returned_files": [Path(item).name for item in outputs],
        "transcription_input": target.name,
        "source_upload_name": source_display_name or source.name,
        "complementary_reference": None,
        "effective_runtime": effective,
        "settings": {
            "architecture": "VR",
            "window_size": settings.uvr_vr_window_size,
            "aggression": settings.uvr_vr_aggression,
            "high_end_process": settings.uvr_vr_high_end_process,
            "output_single_stem": settings.uvr_target_stem,
            "normalization_threshold": 1.0,
            "device": "cpu" if settings.uvr_force_cpu else "automatic",
            "resampler": settings.uvr_resampler,
            "writer": "soundfile",
            "output_format": settings.uvr_output_format,
        },
    }
    (output_dir / "separation-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return target


def separate_wind(
    source: Path,
    output_dir: Path,
    separator_factory=None,
    source_display_name: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> Path:
    """Run UVR in a child process so a local job can actually be cancelled."""
    if separator_factory is not None:
        return _separate_wind_in_process(
            source,
            output_dir,
            separator_factory=separator_factory,
            source_display_name=source_display_name,
        )
    _verify_wind_model(
        Path(settings.uvr_model_dir) / settings.uvr_model_name,
        settings.uvr_model_sha256,
    )
    result_path = output_dir / ".uvr-result.json"
    log_path = output_dir / "uvr-process.log"
    command = [
        sys.executable,
        "-m",
        "backend.uvr_worker",
        str(source),
        str(output_dir),
        source_display_name or source.name,
        str(result_path),
    ]
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(Path(__file__).resolve().parents[2]),
            env=os.environ.copy(),
        )
        while process.poll() is None:
            if cancel_check and cancel_check():
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise JobCancelled("UVR separation cancelled")
            time.sleep(0.2)
    if process.returncode != 0 or not result_path.exists():
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
        raise RuntimeError(f"UVR separation failed.\n{tail or 'No diagnostic output.'}")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    target = Path(payload["path"])
    if not target.exists():
        raise RuntimeError("UVR worker reported an output that does not exist.")
    return target


def transcribe_sax(
    wind_path: Path,
    raw_midi: Path,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    command = shutil.which("midi_transcription")
    if not command:
        raise RuntimeError("midi_transcription is missing. Run scripts/setup-mac.sh again.")
    log_path = raw_midi.with_suffix(".log")
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [command, str(wind_path), str(raw_midi), "--instrument", "saxophone", "--device", settings.sax_device],
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            env=os.environ.copy(),
        )
        started = time.monotonic()
        while process.poll() is None:
            if cancel_check and cancel_check():
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise JobCancelled("Sax transcription cancelled")
            if time.monotonic() - started > 1800:
                process.kill()
                process.wait(timeout=5)
                raise RuntimeError("Sax transcription timed out after 1800 seconds.")
            time.sleep(0.2)
    if process.returncode != 0 or not raw_midi.exists():
        output = log_path.read_text(encoding="utf-8", errors="replace")
        tail = output[-3000:] if output else "No diagnostic output."
        raise RuntimeError(f"Sax transcription failed.\n{tail}")


def _separator_providers() -> dict[str, object]:
    model_path = Path(settings.uvr_model_dir) / settings.uvr_model_name
    return {
        "uvr": UVRSeparatorProvider(separate_wind, model_path),
        "lalal": LalalSeparatorProvider(
            api_key=settings.lalal_api_key,
            base_url=settings.lalal_api_base_url,
            splitter=settings.lalal_splitter,
            extraction_level=settings.lalal_extraction_level,
            poll_seconds=settings.lalal_poll_seconds,
            timeout_seconds=settings.lalal_timeout_seconds,
        ),
    }


def _provider_order(mode: str) -> list[str]:
    if mode not in {"uvr", "lalal", "auto", "both"}:
        raise RuntimeError("SEPARATION_PROVIDER must be one of: uvr, lalal, auto, both.")
    if mode in {"uvr", "lalal"}:
        return [mode]
    primary = settings.separation_primary if settings.separation_primary in {"uvr", "lalal"} else "uvr"
    secondary = "lalal" if primary == "uvr" else "uvr"
    return [primary, secondary]


def _evaluate_stem_candidate(
    candidate: StemCandidate,
    original_path: Path,
    outputs: Path,
    bpm: float,
    selected_instrument: str,
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    label = re.sub(r"[^a-z0-9]+", "-", candidate.provider.lower()).strip("-") or "candidate"
    raw_path = outputs / f"raw-sax-{label}.mid"
    cleaned_path = outputs / f"deterministic-clean-{label}.mid"
    _ensure_active(cancel_check)
    transcribe_sax(candidate.path, raw_path, cancel_check)
    events = clean_performance_midi(raw_path, cleaned_path, bpm, selected_instrument)
    evidence = build_transcription_evidence(original_path, candidate.path, events, bpm)
    quality = candidate_quality(evidence)
    return {
        "candidate": candidate,
        "raw_path": raw_path,
        "cleaned_path": cleaned_path,
        "events": events,
        "evidence": evidence,
        "quality": quality,
    }


def run_pipeline(
    job_dir: Path,
    original_path: Path,
    isolated_path: Path | None,
    title: str,
    artist: str,
    selected_instrument: str,
    use_ai: bool,
    highlight_uncertain: bool,
    progress: Progress,
    original_display_name: str | None = None,
    isolated_display_name: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    outputs = job_dir / "outputs"
    outputs.mkdir(exist_ok=True)

    _ensure_active(cancel_check)
    progress("analyzing", 8, "Analyzing the original recording")
    facts = analyze_audio(original_path)
    _ensure_active(cancel_check)

    candidate_runs: list[dict] = []
    provider_failures: dict[str, str] = {}
    provider_mode = "uploaded" if isolated_path else settings.separation_provider

    if isolated_path:
        progress("separating", 24, "Using your isolated horn stem")
        supplied_path = outputs / "isolated-wind-uploaded.wav"
        shutil.copy2(isolated_path, supplied_path)
        candidate = StemCandidate(
            provider="uploaded",
            path=supplied_path,
            metadata={
                "engine": "supplied-file",
                "source_upload_name": original_display_name or original_path.name,
                "isolated_upload_name": isolated_display_name or isolated_path.name,
            },
        )
        progress("transcribing", 48, "Running the sax-specific MIDI model")
        candidate_runs.append(_evaluate_stem_candidate(candidate, original_path, outputs, facts.bpm, selected_instrument, cancel_check))
    else:
        providers = _separator_providers()
        order = _provider_order(settings.separation_provider)
        for index, provider_name in enumerate(order):
            provider = providers[provider_name]
            should_run = True
            if settings.separation_provider == "auto" and index == 1 and candidate_runs:
                should_run = candidate_runs[0]["quality"] < settings.separation_quality_threshold
            if not should_run:
                break
            if not provider.available():
                provider_failures[provider_name] = "provider is not configured"
                continue
            progress(
                "separating",
                22 + index * 13,
                f"Extracting a wind candidate with {provider_name.upper()}",
            )
            try:
                candidate = provider.separate(
                    original_path,
                    outputs,
                    original_display_name or original_path.name,
                    cancel_check=cancel_check,
                )
                progress(
                    "transcribing",
                    46 + index * 10,
                    f"Transcribing the {provider_name.upper()} wind candidate",
                )
                candidate_runs.append(_evaluate_stem_candidate(candidate, original_path, outputs, facts.bpm, selected_instrument, cancel_check))
            except Exception as exc:
                if cancel_check and cancel_check():
                    raise JobCancelled("Separation cancelled") from exc
                provider_failures[provider_name] = str(exc)
                if settings.separation_provider in {"uvr", "lalal"}:
                    raise

    if not candidate_runs:
        detail = "; ".join(f"{name}: {reason}" for name, reason in provider_failures.items())
        raise RuntimeError(f"No wind separator produced a usable candidate. {detail}")

    _ensure_active(cancel_check)
    progress("comparing", 72, "Selecting the strongest audio-supported transcription")
    selected_run = max(candidate_runs, key=lambda item: item["quality"])
    selected_candidate: StemCandidate = selected_run["candidate"]
    wind_path = selected_candidate.path
    events = selected_run["events"]
    transcription_evidence = selected_run["evidence"]
    separation_mode = "uploaded-horn-stem" if selected_candidate.provider == "uploaded" else f"{selected_candidate.provider}-wind"

    raw_midi = outputs / "raw-sax.mid"
    deterministic_midi = outputs / "deterministic-clean.mid"
    shutil.copy2(selected_run["raw_path"], raw_midi)
    shutil.copy2(selected_run["cleaned_path"], deterministic_midi)

    cross_provider = None
    if len(candidate_runs) >= 2:
        cross_provider = compare_event_sets(candidate_runs[0]["events"], candidate_runs[1]["events"])
    separation_manifest = {
        "mode": provider_mode,
        "primary": settings.separation_primary,
        "quality_threshold": settings.separation_quality_threshold,
        "selected_provider": selected_candidate.provider,
        "selected_transcription_input": wind_path.name,
        "candidates": [
            {
                **item["candidate"].to_dict(),
                "quality_score": item["quality"],
                "candidate_note_count": len(item["events"]),
            }
            for item in candidate_runs
        ],
        "cross_provider_note_agreement": cross_provider,
        "provider_failures": provider_failures,
        "note": "Quality scores are routing heuristics, not claimed transcription-accuracy percentages.",
    }
    (outputs / "separation-manifest.json").write_text(
        json.dumps(separation_manifest, indent=2),
        encoding="utf-8",
    )

    _ensure_active(cancel_check)
    progress("cleaning", 77, "Removing fragments and quantizing the selected monophonic line")
    raw_events = read_midi_events(raw_midi, facts.bpm)
    events, deterministic_range_adjustments = normalize_events_to_written_range(
        events, selected_instrument
    )
    draft_xml = outputs / f"deterministic-draft-{selected_instrument}.musicxml"
    export_musicxml(
        events,
        draft_xml,
        facts.bpm,
        facts.concert_key,
        facts.mode,
        selected_instrument,
        title,
        artist,
        facts.meter,
    )

    if use_ai:
        _ensure_active(cancel_check)
        progress("reviewing", 82, "Running the extra transcription check")
        review_result = review(
            title=title,
            artist=artist,
            facts=facts,
            original_path=original_path,
            wind_path=wind_path,
            raw_events=raw_events,
            cleaned_events=events,
            draft_xml_path=draft_xml,
            transcription_evidence=transcription_evidence,
            model=settings.openai_model,
            audio_model=settings.openai_audio_model,
            audio_chunk_seconds=settings.openai_audio_chunk_seconds,
            audio_max_chunks=settings.openai_audio_max_chunks,
        )
        _ensure_active(cancel_check)
    else:
        review_result = ReviewResult(
            used=False,
            summary="Extra transcription check not selected; local cleanup was used.",
            researched_song={},
            selected_context={},
            corrections=[],
            sources=[],
            audio_comparison=[],
            models={},
        )

    final_events, correction_audit = apply_safe_corrections(
        events,
        review_result.corrections,
        transcription_evidence,
        return_audit=True,
    )
    final_events, final_range_adjustments = normalize_events_to_written_range(
        final_events, selected_instrument
    )
    comparison_events = next(
        (item["events"] for item in candidate_runs if item is not selected_run),
        None,
    )
    final_events = annotate_event_confidence(
        final_events,
        transcription_evidence,
        comparison_events=comparison_events,
    )
    selected_bpm = facts.bpm
    selected_key = facts.concert_key
    selected_mode = facts.mode
    selected_meter = facts.meter
    if review_result.selected_context:
        context = review_result.selected_context
        candidate_bpm = float(context.get("bpm", selected_bpm))
        if selected_bpm * 0.85 <= candidate_bpm <= selected_bpm * 1.15:
            selected_bpm = candidate_bpm
        if context.get("concert_key") in PITCH_NAMES:
            selected_key = context["concert_key"]
        if context.get("mode") in {"major", "minor"}:
            selected_mode = context["mode"]
        if context.get("meter") in {"2/4", "3/4", "4/4", "5/4", "6/8", "9/8", "12/8"}:
            selected_meter = context["meter"]

    _ensure_active(cancel_check)
    progress("exporting", 93, "Writing simple and advanced scores")
    advanced_events = final_events
    simple_events = simplify_events(advanced_events)
    simple_confidence = summarize_event_confidence(simple_events)
    advanced_confidence = summarize_event_confidence(advanced_events)

    simple_midi = outputs / "sax-simple-sounding.mid"
    advanced_midi = outputs / "sax-advanced-sounding.mid"
    write_events_midi(simple_events, simple_midi, selected_bpm, selected_instrument)
    write_events_midi(advanced_events, advanced_midi, selected_bpm, selected_instrument)

    simple_xml = outputs / f"sax-{selected_instrument}-simple.musicxml"
    advanced_xml = outputs / f"sax-{selected_instrument}-advanced.musicxml"
    export_musicxml(
        simple_events,
        simple_xml,
        selected_bpm,
        selected_key,
        selected_mode,
        selected_instrument,
        title,
        artist,
        selected_meter,
        highlight_uncertain=highlight_uncertain,
    )
    export_musicxml(
        advanced_events,
        advanced_xml,
        selected_bpm,
        selected_key,
        selected_mode,
        selected_instrument,
        title,
        artist,
        selected_meter,
        highlight_uncertain=highlight_uncertain,
    )
    simple_pdf = outputs / f"sax-{selected_instrument}-simple.pdf"
    advanced_pdf = outputs / f"sax-{selected_instrument}-advanced.pdf"
    simple_pdf_ready = render_pdf(simple_xml, simple_pdf)
    advanced_pdf_ready = render_pdf(advanced_xml, advanced_pdf)
    rhythm_grid = str(advanced_events[0].get("rhythm_grid", "straight-sixteenth")) if advanced_events else "straight-sixteenth"
    rhythm_grid_beats = float(advanced_events[0].get("rhythm_grid_beats", ADVANCED_GRID_BEATS)) if advanced_events else ADVANCED_GRID_BEATS
    pickup_offset = float(advanced_events[0].get("pickup_offset_beats", 0.0)) if advanced_events else 0.0
    evidence = {
        "song": {"title": title, "artist": artist},
        "audio_analysis": facts.to_dict(),
        "selected_context": {"concert_key": selected_key, "mode": selected_mode, "bpm": round(selected_bpm, 2), "meter": selected_meter},
        "instrument": selected_instrument,
        "playable_range": {
            "deterministic_octave_adjustments": deterministic_range_adjustments,
            "final_octave_adjustments": final_range_adjustments,
        },
        "separation_mode": separation_mode,
        "separation": separation_manifest,
        "confidence": {
            "routing_score": selected_run["quality"],
            "level": "high" if selected_run["quality"] >= 0.82 else "medium" if selected_run["quality"] >= 0.68 else "low",
            "needs_review": selected_run["quality"] < settings.separation_quality_threshold,
        },
        "note_confidence": {
            "highlighting_enabled": highlight_uncertain,
            "method": "Local pitch, voicing, onset, relative wind-stem energy, and optional cross-separator agreement.",
            "limitations": "A stable sung note can resemble a sax pitch. Possible non-horn labels are warnings, not source identification.",
            "colors": {"high": "black", "medium": "orange", "low": "red"},
            "simple": simple_confidence,
            "advanced": advanced_confidence,
            "advanced_notes": [
                {
                    "index": int(event["index"]),
                    "pitch": int(event["pitch"]),
                    "start_beat": float(event["start_beat"]),
                    "confidence_score": float(event["confidence_score"]),
                    "confidence_level": event["confidence_level"],
                    "possible_non_horn": bool(event["possible_non_horn"]),
                    "reasons": event["confidence_reasons"],
                    "flags": event["confidence_flags"],
                }
                for event in advanced_events
            ],
        },
        "notation_versions": {
            "simple": {
                "grid_beats": round(1.0 / 3.0, 6) if rhythm_grid in {"swing-eighth", "triplet-eighth"} else SIMPLE_GRID_BEATS,
                "rhythm_grid": rhythm_grid,
                "note_count": len(simple_events),
                "description": "Recommended readable draft preserving the detected feel on a coarse grid.",
            },
            "advanced": {
                "grid_beats": rhythm_grid_beats,
                "rhythm_grid": rhythm_grid,
                "pickup_offset_beats": pickup_offset,
                "note_count": len(advanced_events),
                "description": "Detailed draft using the selected straight, swing, or triplet grid.",
            },
        },
        "note_counts": {
            "raw": len(raw_events),
            "deterministic": len(events),
            "verified": len(final_events),
            "simple": len(simple_events),
            "advanced": len(advanced_events),
        },
        "transcription_evidence": transcription_evidence,
        "ai_review": review_result.to_dict(),
        "ai_correction_audit": correction_audit,
        "warning": "Machine-assisted draft. Confirm downbeat, key/mode, articulations and octave choices by ear.",
    }
    evidence_path = outputs / "evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    files: list[tuple[Path, str]] = [(wind_path, "wind_stem")]
    for diagnostic_name in ("no-wind-reference.wav", "separation-manifest.json"):
        diagnostic_path = outputs / diagnostic_name
        if diagnostic_path.exists():
            files.append((diagnostic_path, "diagnostic"))
    files.extend([
        (raw_midi, "raw_midi"),
        (deterministic_midi, "deterministic_midi"),
        (draft_xml, "draft_musicxml"),
        (simple_midi, "simple_midi"),
        (simple_xml, "musicxml"),
        (advanced_midi, "advanced_midi"),
        (advanced_xml, "advanced_musicxml"),
        (evidence_path, "evidence"),
    ])
    if simple_pdf_ready:
        files.append((simple_pdf, "simple_pdf"))
    if advanced_pdf_ready:
        files.append((advanced_pdf, "advanced_pdf"))
    progress("complete", 100, "Your editable sax score is ready")
    return {
        "facts": evidence["selected_context"],
        "analysis": facts.to_dict(),
        "separation_mode": separation_mode,
        "selected_provider": selected_candidate.provider,
        "confidence": evidence["confidence"],
        "note_confidence": evidence["note_confidence"],
        "highlight_uncertain_notes": highlight_uncertain,
        "notes": len(simple_events),
        "simple_notes": len(simple_events),
        "advanced_notes": len(advanced_events),
        "rhythm_grid": rhythm_grid,
        "pickup_offset_beats": pickup_offset,
        "ai_verified": review_result.used,
        "ai_summary": (
            review_result.summary
            if not review_result.corrections
            else f"{sum(item['status'] == 'applied' for item in correction_audit)} of {len(review_result.corrections)} AI suggestions passed the local audio guards."
        ),
        "source_audio": original_display_name or original_path.name,
        "isolated_upload": isolated_display_name if isolated_path else None,
        "files": [
            {"name": path.name, "download_name": path.name, "role": role, "size": path.stat().st_size}
            for path, role in files
        ],
    }
