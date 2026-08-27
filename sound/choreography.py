"""Python mirror of engine/choreography.js."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from music_score import canonical_sha256, score_at

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHOREOGRAPHY = ROOT / "render" / "choreography.json"
SCHEMA = "danse.choreography.v1"
MOVEMENT_CUT = {
    "ONE": "solo",
    "ASSEMBLY": "score",
    "DIVISION": "score",
    "PHRASE": "grid",
    "STILLNESS": "figure",
    "RESEED": "bands",
    "SIGNATURE": "black",
}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contract_sha256(choreography: dict[str, Any]) -> str:
    identity = choreography.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("choreography: identity must be a mapping")
    source = copy.deepcopy(choreography)
    source["identity"].pop("contract_sha256", None)
    return canonical_sha256(source)


def load_choreography(
    path: Path = DEFAULT_CHOREOGRAPHY,
    *,
    score: dict[str, Any] | None = None,
    score_path: Path | None = None,
    corpus_manifest: dict[str, Any] | None = None,
    corpus_manifest_path: Path | None = None,
    corpus_score_path: Path | None = None,
) -> dict[str, Any]:
    return validate(
        json.loads(path.read_text()),
        score=score,
        score_file_sha256=file_sha256(score_path) if score_path else None,
        corpus_manifest=corpus_manifest,
        corpus_manifest_sha256=file_sha256(corpus_manifest_path) if corpus_manifest_path else None,
        corpus_score_sha256=file_sha256(corpus_score_path) if corpus_score_path else None,
    )


def validate(
    choreography: Any,
    *,
    score: dict[str, Any] | None = None,
    score_file_sha256: str | None = None,
    corpus_manifest: dict[str, Any] | None = None,
    corpus_manifest_sha256: str | None = None,
    corpus_score_sha256: str | None = None,
) -> dict[str, Any]:
    def bad(message: str) -> None:
        raise ValueError(f"choreography: {message}")

    if not isinstance(choreography, dict):
        bad("root must be a mapping")
    if choreography.get("schema") != SCHEMA:
        bad(f"unknown schema {choreography.get('schema')}")
    identity = choreography.get("identity")
    if not isinstance(identity, dict):
        bad("identity must be a mapping")
    for name in (
        "score_contract_sha256",
        "score_file_sha256",
        "corpus_manifest_sha256",
        "corpus_score_sha256",
        "contract_sha256",
    ):
        value = identity.get(name)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            bad(f"identity.{name} must be SHA-256")
    if identity["contract_sha256"] != contract_sha256(choreography):
        bad("identity.contract_sha256 does not match the choreography content")

    limits = choreography.get("legibility")
    if not isinstance(limits, dict):
        bad("legibility must be a mapping")
    if limits.get("minimum_pose_dwell_bars", 0) < 2:
        bad("minimum_pose_dwell_bars must be at least two")
    if limits.get("minimum_pose_transition_bars", 0) < 1:
        bad("minimum_pose_transition_bars must be at least one")
    if limits.get("minimum_topology_transition_bars", 0) < 1:
        bad("minimum_topology_transition_bars must be at least one")
    maximum = limits.get("maximum_fragment_change_area_per_bar", -1)
    if not 0 <= maximum <= 0.25:
        bad("maximum_fragment_change_area_per_bar must be in [0, 0.25]")
    fraction = limits.get("fragment_counterpoint_fraction", 0)
    if not 0 < fraction <= 0.25:
        bad("fragment_counterpoint_fraction must be in (0, 0.25]")
    if limits.get("counterpoint_groups") != 4:
        bad("counterpoint_groups must be four anatomical cohorts")
    if limits.get("counterpoint_grid_subdivisions_per_beat") != 2:
        bad("counterpoint grid must use score eighth-notes")
    transition_fraction = limits.get("counterpoint_transition_fraction", 0)
    if not 0 < transition_fraction <= 0.5:
        bad("counterpoint_transition_fraction must be in (0, 0.5]")
    if limits.get("hard_global_recast_before_signature") is not False:
        bad("hard global recasts before SIGNATURE are forbidden")
    if limits.get("frame_order_semantics") != "authored-motif-not-chronology":
        bad("frame order semantics are not explicit")

    motifs = choreography.get("motifs")
    if not isinstance(motifs, list) or not motifs:
        bad("motifs must be non-empty")
    motif_by_id: dict[str, dict[str, Any]] = {}
    frame_by_id = {frame["id"]: frame for frame in corpus_manifest.get("frames", [])} if corpus_manifest else None
    for index, motif in enumerate(motifs):
        if not isinstance(motif, dict) or not isinstance(motif.get("id"), str) or not motif["id"]:
            bad(f"motif {index} is malformed")
        if motif["id"] in motif_by_id:
            bad(f"duplicate motif {motif['id']}")
        frames = motif.get("source_frame_ids")
        if not isinstance(frames, list) or not frames or any(not isinstance(frame, str) or not frame for frame in frames):
            bad(f"motif {motif['id']} must contain ordered source_frame_ids")
        if len(set(frames)) != len(frames):
            bad(f"motif {motif['id']} repeats a source frame")
        geometry = motif.get("geometry_frame_id")
        if geometry is not None and geometry not in frames:
            bad(f"motif {motif['id']}.geometry_frame_id must belong to the motif")
        if frame_by_id is not None:
            for frame_id in frames:
                frame = frame_by_id.get(frame_id)
                if frame is None:
                    bad(f"motif {motif['id']} names absent source frame {frame_id}")
                if frame.get("registered") is False:
                    bad(f"motif {motif['id']} names unregistered source frame {frame_id}")
        motif_by_id[motif["id"]] = {**motif, "index": index}

    assignments = choreography.get("phrase_assignments")
    if not isinstance(assignments, list) or not assignments:
        bad("phrase_assignments must be non-empty")
    seen_phrases: set[str] = set()
    for index, assignment in enumerate(assignments):
        if not isinstance(assignment, dict):
            bad(f"phrase assignment {index} is malformed")
        phrase_id = assignment.get("phrase_id")
        if not isinstance(phrase_id, str) or not phrase_id or phrase_id in seen_phrases:
            bad(f"duplicate or missing phrase assignment {phrase_id}")
        seen_phrases.add(phrase_id)
        movement = assignment.get("movement_id")
        if movement not in MOVEMENT_CUT:
            bad(f"{phrase_id} names unknown movement {movement}")
        if assignment.get("cut_mode") != MOVEMENT_CUT[movement]:
            bad(f"{phrase_id} must use {MOVEMENT_CUT[movement]} for {movement}")
        motif = motif_by_id.get(assignment.get("motif_id")) if assignment.get("motif_id") is not None else None
        if assignment.get("motif_id") is not None and motif is None:
            bad(f"{phrase_id} names unknown motif {assignment.get('motif_id')}")
        if movement in {"ASSEMBLY", "SIGNATURE"} and assignment.get("motif_id") is not None:
            bad(f"{movement} must not replace its authored {assignment.get('cut_mode')} material")
        if movement not in {"ASSEMBLY", "SIGNATURE"} and motif is None:
            bad(f"{movement} requires a photographic motif")
        if assignment.get("pose_dwell_bars", 0) < limits["minimum_pose_dwell_bars"]:
            bad(f"{phrase_id} pose dwell is too short")
        if assignment.get("pose_transition_bars", 0) < limits["minimum_pose_transition_bars"]:
            bad(f"{phrase_id} pose transition is too short")
        if assignment.get("topology_transition_bars", 0) < limits["minimum_topology_transition_bars"]:
            bad(f"{phrase_id} topology transition is too short")
        if type(assignment.get("hold_complete_phrase")) is not bool:
            bad(f"{phrase_id} hold_complete_phrase must be boolean")
        if movement in {"ONE", "STILLNESS"} and len(motif["source_frame_ids"]) != 1:
            bad(f"{movement} must name exactly one readable source frame")

    for movement in ("ONE", "ASSEMBLY", "STILLNESS"):
        owned = [assignment for assignment in assignments if assignment["movement_id"] == movement]
        if not owned or not any(assignment["hold_complete_phrase"] for assignment in owned):
            bad(f"{movement} must include at least one complete-phrase hold")
    if any(
        assignment["movement_id"] == "SIGNATURE" and not assignment["hold_complete_phrase"]
        for assignment in assignments
    ):
        bad("SIGNATURE must hold black through its complete phrase")
    for current, following in zip(assignments, assignments[1:]):
        changes = (
            current["movement_id"] != following["movement_id"]
            or current["cut_mode"] != following["cut_mode"]
            or current["motif_id"] != following["motif_id"]
        )
        if changes and current["hold_complete_phrase"]:
            bad(f"{current['phrase_id']} cannot own an outgoing transition while holding its complete phrase")

    if score is not None:
        if identity["score_contract_sha256"] != score.get("identity", {}).get("contract_sha256"):
            bad("score contract digest does not match")
        if score_file_sha256 and identity["score_file_sha256"] != score_file_sha256:
            bad("score file digest does not match")
        if [assignment["phrase_id"] for assignment in assignments] != [phrase["id"] for phrase in score["phrases"]]:
            bad("phrase assignments must match score phrase order exactly")
        if score.get("time", {}).get("passage_mapping") != "native-tempo":
            bad("production choreography requires a native-tempo score")
        last = assignments[-1]
        last_phrase = score["phrases"][-1]
        if last["movement_id"] != "SIGNATURE" or last["cut_mode"] != "black":
            bad("last phrase must be SIGNATURE black")
        if abs(float(last_phrase["end_second"]) - float(last_phrase["start_second"]) - 4) > 1e-6:
            bad("SIGNATURE phrase must last exactly four seconds")
    for label, declared, actual in (
        ("corpus manifest", identity["corpus_manifest_sha256"], corpus_manifest_sha256),
        ("corpus score", identity["corpus_score_sha256"], corpus_score_sha256),
    ):
        if actual and declared != actual:
            bad(f"{label} digest does not match")
    return choreography


def _bar_coordinate(music: dict[str, Any]) -> float:
    return (float(music["beat"]["bar"]) - 1) + (
        (float(music["beat"]["beat_in_bar"]) - 1) + float(music["beat"]["phase"])
    ) / float(music["meter"]["numerator"])


def _phrase_edge_coordinate(score: dict[str, Any], phrase: dict[str, Any], edge: str) -> float:
    duration = float(score["time"]["duration_seconds"])
    second = float(phrase["start_second"])
    if edge == "end":
        second = max(second, min(duration, float(phrase["end_second"])) - 1e-8)
    state = score_at(score, second)
    coordinate = _bar_coordinate(state)
    if edge == "end":
        coordinate += 1e-8 * float(state["tempo"]["effective_bpm"]) / 60 / float(state["meter"]["numerator"])
    nearest = round(coordinate)
    return float(nearest) if abs(coordinate - nearest) < 1e-6 else coordinate


def _smooth(value: float) -> float:
    x = min(1.0, max(0.0, value))
    return x * x * (3 - 2 * x)


def _motif_pose(
    motif: dict[str, Any] | None,
    assignment: dict[str, Any],
    bars: float,
    usable_bars: float = math.inf,
) -> dict[str, Any]:
    if motif is None:
        return {"index": 0, "current": None, "next": None, "blend": 0.0}
    frames = motif["source_frame_ids"]
    if assignment["hold_complete_phrase"] or len(frames) == 1:
        return {"index": 0, "current": frames[0], "next": frames[0], "blend": 0.0}
    hold = float(assignment["pose_dwell_bars"])
    transition = float(assignment["pose_transition_bars"])
    span = hold + transition
    transitions = (
        max(0, math.floor((max(0.0, usable_bars) - hold + 1e-9) / span))
        if math.isfinite(usable_bars)
        else math.inf
    )
    cycle = max(0, min(transitions, math.floor(bars / span)))
    phase = max(0.0, bars - cycle * span)
    current = frames[cycle % len(frames)]
    following = frames[(cycle + 1) % len(frames)]
    blend = (
        0.0
        if cycle >= transitions or phase < hold or current == following
        else _smooth((phase - hold) / transition)
    )
    return {"index": cycle, "current": current, "next": following, "blend": blend}


def _panel_counterpoint(
    score: dict[str, Any],
    motif: dict[str, Any] | None,
    assignment: dict[str, Any],
    music: dict[str, Any],
    limits: dict[str, Any],
) -> dict[str, Any] | None:
    if (
        motif is None
        or assignment["hold_complete_phrase"]
        or len(motif["source_frame_ids"]) < 2
        or assignment["movement_id"] not in {"PHRASE", "RESEED"}
    ):
        return None
    frames = motif["source_frame_ids"]
    groups = int(limits["counterpoint_groups"])
    subdivisions = int(limits["counterpoint_grid_subdivisions_per_beat"])
    rhythm = [0, 3, 1, 3, 2, 3]
    phrase_start = score_at(score, float(music["phrase"]["start_second"]))

    def slot_at(state: dict[str, Any]) -> int:
        return int(state["beat"]["index"]) * subdivisions + math.floor(float(state["beat"]["phase"]) * subdivisions + 1e-9)

    local_slot = max(0, slot_at(music) - slot_at(phrase_start))
    slot_phase = (float(music["beat"]["phase"]) * subdivisions) % 1
    active_group = rhythm[local_slot % len(rhythm)]
    changing = slot_phase < float(limits["counterpoint_transition_fraction"])

    def occurrences(group: int, inclusive_slot: int) -> int:
        slots = inclusive_slot + 1
        whole, remainder = divmod(slots, len(rhythm))
        return whole * rhythm.count(group) + rhythm[:remainder].count(group)
    choices = []
    for group in range(groups):
        updates = occurrences(group, local_slot)
        completed = max(0, updates - 1) if active_group == group and changing else updates
        def frame_index(count: int) -> int:
            return 0 if count == 0 else group + 1 + groups * (count - 1)

        current_index = frame_index(completed)
        next_index = frame_index(completed + 1)
        group_changing = active_group == group and changing
        choices.append(
            {
                "group": group,
                "current_source_frame_id": frames[current_index % len(frames)],
                "next_source_frame_id": frames[next_index % len(frames)] if group_changing else frames[current_index % len(frames)],
                "blend": _smooth(slot_phase / float(limits["counterpoint_transition_fraction"])) if group_changing else 0.0,
            }
        )
    return {
        "groups": choices,
        "active_group": active_group,
        "fragment_change_fraction": float(limits["fragment_counterpoint_fraction"]) if changing else 0.0,
    }


def pose_at(
    score: dict[str, Any],
    choreography: dict[str, Any],
    seed: int,
    t: float,
    window: dict[str, float] | None = None,
) -> dict[str, Any]:
    if type(seed) is not int or not 0 <= seed <= 0xFFFFFFFF:
        raise ValueError("choreography seed must be uint32")
    music = score_at(score, t, window)
    phrase_index = int(music["phrase"]["index"])
    assignment = choreography["phrase_assignments"][phrase_index]
    if assignment["phrase_id"] != music["phrase"]["id"]:
        raise ValueError(f"choreography: no ordered assignment for score phrase {music['phrase']['id']}")
    motif_by_id = {motif["id"]: motif for motif in choreography["motifs"]}
    motif = motif_by_id.get(assignment["motif_id"]) if assignment["motif_id"] is not None else None
    movement_first = phrase_index
    movement_last = phrase_index
    while movement_first > 0 and choreography["phrase_assignments"][movement_first - 1]["movement_id"] == assignment["movement_id"]:
        movement_first -= 1
    while movement_last + 1 < len(choreography["phrase_assignments"]) and choreography["phrase_assignments"][movement_last + 1]["movement_id"] == assignment["movement_id"]:
        movement_last += 1
    movement_start = float(score["phrases"][movement_first]["start_second"])
    movement_end = float(score["phrases"][movement_last]["end_second"])
    movement_u = min(1.0, max(0.0, (float(music["source_second"]) - movement_start) / (movement_end - movement_start)))
    start_bar = _phrase_edge_coordinate(score, music["phrase"], "start")
    end_bar = _phrase_edge_coordinate(score, music["phrase"], "end")
    at_bar = _bar_coordinate(music)
    bars = max(0.0, at_bar - start_bar)
    next_assignment = choreography["phrase_assignments"][phrase_index + 1] if phrase_index + 1 < len(choreography["phrase_assignments"]) else None
    topology_changes = bool(
        next_assignment is not None
        and (
            next_assignment["cut_mode"] != assignment["cut_mode"]
            or next_assignment["movement_id"] != assignment["movement_id"]
        )
    )
    outgoing_transition_bars = (
        max(
            float(assignment["pose_transition_bars"]),
            float(assignment["topology_transition_bars"]) if topology_changes else 0.0,
        )
        if next_assignment is not None
        and assignment["movement_id"] != "SIGNATURE"
        and not assignment["hold_complete_phrase"]
        else 0.0
    )
    usable_bars = max(0.0, end_bar - start_bar - outgoing_transition_bars)
    pose = _motif_pose(motif, assignment, bars, usable_bars)
    current_cut = assignment["cut_mode"]
    next_cut = current_cut
    transition_kind = None
    transition_bars = float(assignment["pose_transition_bars"])
    transition_progress = float(pose["blend"])
    next_movement_id = assignment["movement_id"]
    current_geometry = motif.get("geometry_frame_id") if motif else None
    next_geometry = current_geometry
    counterpoint = _panel_counterpoint(score, motif, assignment, music, choreography["legibility"])

    if next_assignment is not None and assignment["movement_id"] != "SIGNATURE" and not assignment["hold_complete_phrase"]:
        boundary_bars = outgoing_transition_bars
        remaining = max(0.0, end_bar - at_bar)
        if remaining <= boundary_bars + 1e-9:
            frozen = _motif_pose(
                motif,
                assignment,
                max(0.0, end_bar - boundary_bars - start_bar),
                usable_bars,
            )
            next_motif = motif_by_id.get(next_assignment["motif_id"]) if next_assignment["motif_id"] is not None else None
            arriving = _motif_pose(next_motif, next_assignment, 0)
            progress = _smooth((boundary_bars - remaining) / boundary_bars)
            pose = {
                "index": frozen["index"],
                "current": frozen["next"] if frozen["blend"] >= 0.5 else frozen["current"],
                "next": arriving["current"],
                "blend": progress,
            }
            next_cut = next_assignment["cut_mode"]
            next_movement_id = next_assignment["movement_id"]
            next_geometry = next_motif.get("geometry_frame_id") if next_motif else None
            transition_kind = "topology" if topology_changes else "pose"
            transition_bars = boundary_bars
            transition_progress = progress

    def rounded(value: float) -> float | int:
        result = round(float(value), 12)
        return int(result) if result == int(result) else result

    return {
        "identity": choreography["identity"]["contract_sha256"],
        "seed": seed,
        "motif": motif["id"] if motif else None,
        "pose_index": pose["index"],
        "current_source_frame_id": pose["current"],
        "next_source_frame_id": pose["next"],
        "blend": rounded(pose["blend"]),
        "movement_id": assignment["movement_id"],
        "next_movement_id": next_movement_id,
        "movement_start_second": score["phrases"][movement_first]["start_second"],
        "movement_end_second": score["phrases"][movement_last]["end_second"],
        "movement_u": rounded(movement_u),
        "cut_mode": assignment["cut_mode"],
        "current_cut_mode": current_cut,
        "next_cut_mode": next_cut,
        "current_geometry_frame_id": current_geometry,
        "next_geometry_frame_id": next_geometry,
        "phrase": {
            "id": music["phrase"]["id"],
            "index": phrase_index,
            "start_second": music["phrase"]["start_second"],
            "end_second": music["phrase"]["end_second"],
            "bars_elapsed": rounded(bars),
        },
        "beat": {
            "index": music["beat"]["index"],
            "bar": music["beat"]["bar"],
            "beat_in_bar": music["beat"]["beat_in_bar"],
            "phase": rounded(music["beat"]["phase"]),
            "downbeat": music["beat"]["downbeat"],
        },
        "transition": {
            "active": transition_kind is not None or pose["blend"] > 0,
            "kind": transition_kind if transition_kind is not None else ("pose" if pose["blend"] > 0 else None),
            "bars": rounded(transition_bars),
            "progress": rounded(transition_progress),
            "fragment_change_fraction": rounded(counterpoint["fragment_change_fraction"]) if counterpoint else 0,
        },
        "panel_counterpoint": (
            {
                "active_group": counterpoint["active_group"],
                "fragment_change_fraction": rounded(counterpoint["fragment_change_fraction"]),
                "groups": [{**group, "blend": rounded(group["blend"])} for group in counterpoint["groups"]],
            }
            if counterpoint
            else None
        ),
    }
