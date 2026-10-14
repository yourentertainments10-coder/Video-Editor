"""Timeline data contracts: legacy MVP v0.1 and additive editable v2.0.

The Timeline Document is the single contract shared by every other module:
the cutter and baseline (Step 7) will write it, the renderer (Step 4) and
contact sheet will read it, the evaluator (Step 9) will compare two of them.
This module defines *what a Timeline is*. It does not check cross-field
invariants (contiguity, on-beat alignment, source-range bounds, ...) -- that
is the Timeline Validator, built separately in Step 3 as
``poc/timeline/validate.py``. This file only enforces per-field constraints
that Pydantic can check in isolation (types, ranges, required-ness).

Schema is exactly the v0.1 shape defined in docs/MVP.md Section 9, which maps
onto the full v1 schema in docs/ARCHITECTURE.md Section 12.2 by wrapping
``clips`` into ``tracks[0].clips``, adding a constant ``speed: {kind:
"constant", rate: 1.0}``, and converting frame indices to milliseconds using
``settings.fps``. No information is lost in either direction, so nothing here
should be considered a permanent format -- it is the smallest correct subset
of the eventual one.

Time bases (docs/MVP.md Section 9 and docs/ARCHITECTURE_REVIEW.md item 2):
  timeline positions  -> integer frames at settings.fps (exact grid equality,
                         no millisecond drift on grid snapping)
  source positions    -> integer milliseconds (source files have their own
                         clocks; FFmpeg seeks in seconds)
Speed is always 1.0 in v0, so a clip's source out-point is implied by its
frame length and is not stored separately.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

TIMELINE_FPS = 30


def ms_to_frame(ms: float, fps: int = TIMELINE_FPS) -> int:
    """Convert a millisecond position to the nearest timeline frame."""
    return int(round(ms * fps / 1000.0))


def frame_to_ms(frame: int, fps: int = TIMELINE_FPS) -> int:
    """Convert a timeline frame index to milliseconds."""
    return int(round(frame * 1000.0 / fps))


def frames_to_seconds(frames: int, fps: int = TIMELINE_FPS) -> float:
    """Convert a frame count to seconds (for FFmpeg's ``-t``/``-ss``)."""
    return frames / fps


class _Strict(BaseModel):
    """Shared base: rejects any field not explicitly declared below.

    Not present in the docs/MVP.md reference snippet -- called out explicitly
    per the "flag assumptions" instruction rather than applied silently. See
    the ambiguity note in the handoff message for the reasoning and how to
    revert it if unwanted.
    """

    model_config = ConfigDict(extra="forbid")


class Settings(_Strict):
    """Output frame rate and frame size. One video track, one resolution."""

    fps: int = TIMELINE_FPS
    width: int = 1920
    height: int = 1080


class Music(_Strict):
    """The single music track and the window of it used by this timeline.

    ``in_ms`` is the position in the track that lines up with timeline frame
    0. There is no ``out_ms``: the window length is implied by the timeline's
    ``duration_frames`` (v0 has one track, one tempo, one window -- see
    docs/MVP.md Section 9, which omits ``out_ms`` deliberately).
    """

    path: str
    in_ms: int = 0
    gain_db: float = -1.0
    fade_out_ms: int = 1000


class Beat(_Strict):
    """One informational grid marker. Beats are not clip boundaries by
    themselves; the validator's ON_BEAT check (Step 3) is what ties clip
    starts to this list.
    """

    frame: int
    downbeat: bool = False
    energy: float = Field(ge=0.0, le=1.0)


class Source(_Strict):
    """A footage file and the millisecond offset a clip starts reading from."""

    path: str
    in_ms: int = Field(ge=0)


class Clip(_Strict):
    """One contiguous segment of the output, sourced from one footage file.

    ``score`` and ``rationale`` are optional and populated by the cutter
    (Step 7); a hand-written or baseline clip may omit them.
    """

    id: str
    start_frame: int = Field(ge=0)
    end_frame: int  # exclusive
    source: Source
    score: float | None = None
    rationale: dict[str, float] = Field(default_factory=dict)

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame

    def source_out_ms(self, fps: int = TIMELINE_FPS) -> int:
        """The source-file millisecond position this clip reads up to."""
        return self.source.in_ms + frame_to_ms(self.frames, fps)


class Provenance(_Strict):
    """Which generator produced this timeline, and with what parameters.

    This is how an evaluator (Step 9) tells the scored cutter output apart
    from the random baseline without trusting the output filename.
    """

    generator: Literal["hand", "baseline-v0", "cutter-v0"] = "hand"
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


class Timeline(_Strict):
    """The Timeline Document: the canonical, versioned source of truth.

    Nothing about the rendered video is authoritative; this document is.
    """

    schema_version: Literal["0.1"] = "0.1"
    settings: Settings = Field(default_factory=Settings)
    duration_frames: int = Field(gt=0)
    music: Music
    beats: list[Beat] = Field(default_factory=list)
    clips: list[Clip] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)

    # ---- persistence --------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "Timeline":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> None:
        """Write canonical, indented, newline-terminated JSON.

        Field order follows model declaration order, which Pydantic v2 keeps
        stable across calls and across processes for a given class
        definition -- this is what "deterministic serialization" means here
        (see the ambiguity note: this is not byte-for-byte canonicalization
        such as key sorting, which nothing in the docs asks for).
        """
        Path(path).write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")

    # ---- convenience ----------------------------------------------------

    @property
    def duration_ms(self) -> int:
        return frame_to_ms(self.duration_frames, self.settings.fps)

    def sorted_clips(self) -> list[Clip]:
        return sorted(self.clips, key=lambda c: c.start_frame)


MediaKind = Literal["video", "image", "audio", "music", "text"]
TrackKind = Literal["video", "overlay", "audio", "music"]
TransitionId = Literal[
    "cut", "fade", "dissolve", "dip_to_black", "dip_to_white",
    "wipe_left", "wipe_right", "wipe_up", "wipe_down",
    # Phase 3: FFmpeg xfade transitions (no parameters)
    "slide_left", "slide_right", "slide_up", "slide_down", "zoom_through", "blur_through",
    "circle_open", "circle_close", "radial", "pixelize", "smooth_left", "smooth_right",
    "squeeze", "slice", "wind", "diagonal", "fade_mono",
]
EffectId = Literal[
    "fade_in", "fade_out", "zoom_in", "zoom_out",
    "pan_left", "pan_right", "pan_up", "pan_down",
    "brightness", "contrast", "saturation", "blur",
    # Phase 3: looks, textures, beat accents, repair
    "look_cinematic", "look_vintage", "look_warm", "look_cool", "look_punchy", "look_mono", "look_matte",
    "vignette", "grain", "sharpen", "glow", "rgb_split",
    "flash", "shake", "zoom_punch", "stabilize", "light_leak",
]


class TimeBase(_Strict):
    """Integer timeline ticks per second; the editor document defaults to ms."""

    ticks_per_second: int = Field(default=1000, gt=0)


class EditorSettings(_Strict):
    fps_num: int = Field(default=30, gt=0)
    fps_den: int = Field(default=1, gt=0)
    width: int = Field(default=1920, gt=0)
    height: int = Field(default=1080, gt=0)


class MediaAsset(_Strict):
    """Stable reference to imported media or a text payload."""

    asset_id: str = Field(min_length=1)
    kind: MediaKind
    uri: str | None = None
    text: str | None = None
    duration_ms: int | None = Field(default=None, gt=0)
    mime_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_payload(self) -> "MediaAsset":
        if self.kind == "text":
            if not self.text:
                raise ValueError("text assets require non-empty text")
            if self.uri is not None:
                raise ValueError("text assets cannot have a media uri")
        elif not self.uri:
            raise ValueError(f"{self.kind} assets require a uri")
        elif self.text is not None:
            raise ValueError(f"{self.kind} assets cannot contain text payloads")
        return self


class Layer(_Strict):
    layer_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    order: int = Field(ge=0)
    visible: bool = True
    locked: bool = False


class Track(_Strict):
    track_id: str = Field(min_length=1)
    kind: TrackKind
    layer_id: str = Field(min_length=1)
    name: str = ""
    muted: bool = False


class CropRect(_Strict):
    """Normalized source crop rectangle, expressed as fractions of the frame."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _fits_unit_frame(self) -> "CropRect":
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError("crop rectangle must fit within the normalized source frame")
        return self


class Transform(_Strict):
    crop: CropRect | None = None
    scale_x: float = Field(default=1.0, gt=0.0)
    scale_y: float = Field(default=1.0, gt=0.0)
    position_x: float = 0.0
    position_y: float = 0.0
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)


class AudioProperties(_Strict):
    gain_db: float = 0.0
    pan: float = Field(default=0.0, ge=-1.0, le=1.0)
    muted: bool = False
    fade_in_ms: int = Field(default=0, ge=0)
    fade_out_ms: int = Field(default=0, ge=0)


class FadeTransitionParameters(_Strict):
    curve: Literal["linear", "ease_in", "ease_out"] = "linear"


class DissolveTransitionParameters(_Strict):
    mask_seed: int
    softness: float = Field(default=0.0, ge=0.0, le=1.0)


class DipTransitionParameters(_Strict):
    peak_fraction: float = Field(default=0.5, ge=0.1, le=0.9)
    hold_fraction: float = Field(default=0.0, ge=0.0, le=0.5)


class WipeTransitionParameters(_Strict):
    softness: float = Field(default=0.0, ge=0.0, le=0.5)


TransitionParameters = (
    FadeTransitionParameters
    | DissolveTransitionParameters
    | DipTransitionParameters
    | WipeTransitionParameters
)


class TransitionRef(_Strict):
    """A transition at the incoming boundary immediately before its clip."""

    transition_id: TransitionId
    duration_ticks: int = Field(default=500, ge=0, le=30000)
    parameters: TransitionParameters | None = None
    boundary: Literal["incoming"] = "incoming"

    @model_validator(mode="before")
    @classmethod
    def _parse_transition_parameters(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        transition_id = value.get("transition_id")
        parameter_model = _TRANSITION_PARAMETER_MODELS.get(transition_id)
        parsed = dict(value)
        if transition_id == "cut":
            if parsed.get("duration_ticks") is None:
                parsed["duration_ticks"] = 0
            if parsed.get("parameters") is not None:
                raise ValueError("cut transitions do not accept parameters")
            return parsed
        if transition_id in TransitionId.__args__ and parsed.get("duration_ticks") is None:
            parsed["duration_ticks"] = 500
        if parameter_model is not None:
            raw_parameters = parsed.get("parameters", {})
            if isinstance(raw_parameters, BaseModel):
                raw_parameters = raw_parameters.model_dump()
            parsed["parameters"] = parameter_model.model_validate(raw_parameters)
        return parsed

    @model_validator(mode="after")
    def _duration_matches_kind(self) -> "TransitionRef":
        if self.transition_id == "cut" and self.duration_ticks != 0:
            raise ValueError("cut transition duration_ticks must be 0")
        if self.transition_id != "cut" and self.duration_ticks <= 0:
            raise ValueError("non-cut transition duration_ticks must be greater than 0")
        return self


class TransitionParameterField(_Strict):
    name: str
    value_type: Literal["integer", "number", "string"]
    required: bool
    default: int | float | str | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None


class TransitionMetadata(_Strict):
    transition_id: TransitionId
    name: str
    parameter_schema: tuple[TransitionParameterField, ...]
    duration_default_ticks: int
    duration_min_ticks: int
    duration_max_ticks: int
    renderable: bool
    deterministic: bool
    behavior_contract: str


_TRANSITION_PARAMETER_MODELS: dict[str, type[BaseModel]] = {
    "fade": FadeTransitionParameters,
    "dissolve": DissolveTransitionParameters,
    "dip_to_black": DipTransitionParameters,
    "dip_to_white": DipTransitionParameters,
    "wipe_left": WipeTransitionParameters,
    "wipe_right": WipeTransitionParameters,
    "wipe_up": WipeTransitionParameters,
    "wipe_down": WipeTransitionParameters,
}


def _transition_field(
    name: str,
    value_type: Literal["integer", "number", "string"],
    *,
    required: bool = False,
    default: int | float | str | None = None,
    minimum: int | float | None = None,
    maximum: int | float | None = None,
) -> TransitionParameterField:
    return TransitionParameterField(
        name=name, value_type=value_type, required=required, default=default,
        minimum=minimum, maximum=maximum,
    )


_FADE_TRANSITION_SCHEMA = (_transition_field("curve", "string", default="linear"),)
_DISSOLVE_TRANSITION_SCHEMA = (
    _transition_field("mask_seed", "integer", required=True),
    _transition_field("softness", "number", default=0.0, minimum=0.0, maximum=1.0),
)
_DIP_TRANSITION_SCHEMA = (
    _transition_field("peak_fraction", "number", default=0.5, minimum=0.1, maximum=0.9),
    _transition_field("hold_fraction", "number", default=0.0, minimum=0.0, maximum=0.5),
)
_WIPE_TRANSITION_SCHEMA = (_transition_field("softness", "number", default=0.0, minimum=0.0, maximum=0.5),)

TRANSITION_CATALOGUE: tuple[TransitionMetadata, ...] = (
    TransitionMetadata(transition_id="cut", name="Cut", parameter_schema=(), duration_default_ticks=0, duration_min_ticks=0, duration_max_ticks=0, renderable=True, deterministic=True, behavior_contract="Instant boundary change with no overlap or generated frames."),
    TransitionMetadata(transition_id="fade", name="Fade", parameter_schema=_FADE_TRANSITION_SCHEMA, duration_default_ticks=500, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="Linear or eased blend between outgoing and incoming scenes over duration_ticks."),
    TransitionMetadata(transition_id="dissolve", name="Dissolve", parameter_schema=_DISSOLVE_TRANSITION_SCHEMA, duration_default_ticks=500, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="Per-pixel dissolve using a fixed mask_seed and softness over duration_ticks."),
    TransitionMetadata(transition_id="dip_to_black", name="Dip to Black", parameter_schema=_DIP_TRANSITION_SCHEMA, duration_default_ticks=500, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="Fade outgoing scene to black then reveal incoming scene using peak_fraction and hold_fraction."),
    TransitionMetadata(transition_id="dip_to_white", name="Dip to White", parameter_schema=_DIP_TRANSITION_SCHEMA, duration_default_ticks=500, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="Fade outgoing scene to white then reveal incoming scene using peak_fraction and hold_fraction."),
    TransitionMetadata(transition_id="wipe_left", name="Wipe Left", parameter_schema=_WIPE_TRANSITION_SCHEMA, duration_default_ticks=500, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="Reveal incoming scene with a deterministic left-moving boundary over duration_ticks."),
    TransitionMetadata(transition_id="wipe_right", name="Wipe Right", parameter_schema=_WIPE_TRANSITION_SCHEMA, duration_default_ticks=500, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="Reveal incoming scene with a deterministic right-moving boundary over duration_ticks."),
    TransitionMetadata(transition_id="wipe_up", name="Wipe Up", parameter_schema=_WIPE_TRANSITION_SCHEMA, duration_default_ticks=500, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="Reveal incoming scene with a deterministic upward-moving boundary over duration_ticks."),
    TransitionMetadata(transition_id="wipe_down", name="Wipe Down", parameter_schema=_WIPE_TRANSITION_SCHEMA, duration_default_ticks=500, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="Reveal incoming scene with a deterministic downward-moving boundary over duration_ticks."),
    TransitionMetadata(transition_id="slide_left", name="Slide Left", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="The incoming scene slides in from the right, pushing nothing; the outgoing scene stays."),
    TransitionMetadata(transition_id="slide_right", name="Slide Right", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="The incoming scene slides in from the left."),
    TransitionMetadata(transition_id="slide_up", name="Slide Up", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="The incoming scene slides in from the bottom."),
    TransitionMetadata(transition_id="slide_down", name="Slide Down", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="The incoming scene slides in from the top."),
    TransitionMetadata(transition_id="zoom_through", name="Zoom Through", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="The outgoing scene zooms toward the viewer and gives way to the incoming scene."),
    TransitionMetadata(transition_id="blur_through", name="Blur Through", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="A fast horizontal blur hides the cut, like a whip pan."),
    TransitionMetadata(transition_id="circle_open", name="Circle Open", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="The incoming scene opens from a growing circle in the centre."),
    TransitionMetadata(transition_id="circle_close", name="Circle Close", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="The outgoing scene closes into a shrinking circle."),
    TransitionMetadata(transition_id="radial", name="Radial", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="A clock-hand sweep reveals the incoming scene."),
    TransitionMetadata(transition_id="pixelize", name="Pixelize", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="Both scenes break into blocks and reassemble."),
    TransitionMetadata(transition_id="smooth_left", name="Soft Wipe Left", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="A wipe to the left with a soft edge."),
    TransitionMetadata(transition_id="smooth_right", name="Soft Wipe Right", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="A wipe to the right with a soft edge."),
    TransitionMetadata(transition_id="squeeze", name="Squeeze", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="The outgoing scene squeezes away horizontally."),
    TransitionMetadata(transition_id="slice", name="Slice", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="Horizontal slices reveal the incoming scene."),
    TransitionMetadata(transition_id="wind", name="Wind", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="The outgoing scene blows away in streaks."),
    TransitionMetadata(transition_id="diagonal", name="Diagonal Wipe", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="A diagonal wipe from the top-left corner."),
    TransitionMetadata(transition_id="fade_mono", name="Fade Through Grey", parameter_schema=(), duration_default_ticks=400, duration_min_ticks=1, duration_max_ticks=30000, renderable=True, deterministic=True, behavior_contract="Both scenes lose their colour during the blend."),
)


def get_transition_metadata(transition_id: TransitionId) -> TransitionMetadata:
    for metadata in TRANSITION_CATALOGUE:
        if metadata.transition_id == transition_id:
            return metadata
    raise KeyError(f"unknown transition id: {transition_id}")


def serialize_transition_catalogue() -> str:
    return json.dumps(
        [item.model_dump(mode="json") for item in TRANSITION_CATALOGUE],
        sort_keys=True,
        separators=(",", ":"),
    )


class FadeParameters(_Strict):
    duration_ms: int = Field(default=300, ge=1, le=5000)


class ZoomInParameters(_Strict):
    from_scale: float = Field(default=1.0, ge=0.1, le=4.0)
    to_scale: float = Field(default=1.2, ge=0.1, le=4.0)
    duration_ms: int = Field(default=1000, ge=1, le=30000)

    @model_validator(mode="after")
    def _increases_scale(self) -> "ZoomInParameters":
        if self.to_scale < self.from_scale:
            raise ValueError("zoom_in to_scale must be greater than or equal to from_scale")
        return self


class ZoomOutParameters(_Strict):
    from_scale: float = Field(default=1.2, ge=0.1, le=4.0)
    to_scale: float = Field(default=1.0, ge=0.1, le=4.0)
    duration_ms: int = Field(default=1000, ge=1, le=30000)

    @model_validator(mode="after")
    def _decreases_scale(self) -> "ZoomOutParameters":
        if self.to_scale > self.from_scale:
            raise ValueError("zoom_out to_scale must be less than or equal to from_scale")
        return self


class PanParameters(_Strict):
    distance: float = Field(default=0.1, ge=0.0, le=0.5)
    duration_ms: int = Field(default=1000, ge=1, le=30000)


class BrightnessParameters(_Strict):
    amount: float = Field(ge=-1.0, le=1.0)


class ContrastParameters(_Strict):
    factor: float = Field(default=1.0, ge=0.0, le=3.0)


class SaturationParameters(_Strict):
    factor: float = Field(default=1.0, ge=0.0, le=3.0)


class BlurParameters(_Strict):
    radius: float = Field(ge=0.0, le=50.0)


class AmountParameters(_Strict):
    """Strength of a look, texture or repair effect (0 = none, 1 = strong)."""
    amount: float = Field(default=0.6, ge=0.0, le=1.0)


class AccentParameters(_Strict):
    """A short accent (flash, shake, zoom punch) at at_ms from the start of the clip."""
    at_ms: int = Field(default=0, ge=0, le=600000)
    duration_ms: int = Field(default=300, ge=1, le=5000)
    amount: float = Field(default=0.6, ge=0.0, le=1.0)


EffectParameters = (
    FadeParameters
    | ZoomInParameters
    | ZoomOutParameters
    | PanParameters
    | BrightnessParameters
    | ContrastParameters
    | SaturationParameters
    | BlurParameters
    | AmountParameters
    | AccentParameters
)


class EffectRef(_Strict):
    """Allowlisted effect specification with effect-specific typed parameters."""

    effect_id: EffectId
    parameters: EffectParameters

    @model_validator(mode="before")
    @classmethod
    def _parse_effect_parameters(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        effect_id = value.get("effect_id")
        parameter_model: type[BaseModel] | None = _EFFECT_PARAMETER_MODELS.get(effect_id)
        if parameter_model is None:
            return value
        parsed = dict(value)
        raw_parameters = parsed.get("parameters", {})
        if isinstance(raw_parameters, BaseModel):
            raw_parameters = raw_parameters.model_dump()
        parsed["parameters"] = parameter_model.model_validate(raw_parameters)
        return parsed


class EffectParameterField(_Strict):
    name: str
    value_type: Literal["integer", "number"]
    required: bool
    default: int | float | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None


class EffectMetadata(_Strict):
    effect_id: EffectId
    name: str
    parameter_schema: tuple[EffectParameterField, ...]
    renderable: bool
    deterministic: bool
    behavior_contract: str


_EFFECT_PARAMETER_MODELS: dict[str, type[BaseModel]] = {
    "fade_in": FadeParameters,
    "fade_out": FadeParameters,
    "zoom_in": ZoomInParameters,
    "zoom_out": ZoomOutParameters,
    "pan_left": PanParameters,
    "pan_right": PanParameters,
    "pan_up": PanParameters,
    "pan_down": PanParameters,
    "brightness": BrightnessParameters,
    "contrast": ContrastParameters,
    "saturation": SaturationParameters,
    "blur": BlurParameters,
    **{effect_id: AmountParameters for effect_id in (
        "look_cinematic", "look_vintage", "look_warm", "look_cool", "look_punchy", "look_mono", "look_matte",
        "vignette", "grain", "sharpen", "glow", "rgb_split", "stabilize")},
    **{effect_id: AccentParameters for effect_id in ("flash", "shake", "zoom_punch", "light_leak")},
}


def _field_definition(
    name: str,
    value_type: Literal["integer", "number"],
    *,
    required: bool = False,
    default: int | float | None = None,
    minimum: int | float | None = None,
    maximum: int | float | None = None,
) -> EffectParameterField:
    return EffectParameterField(
        name=name, value_type=value_type, required=required, default=default,
        minimum=minimum, maximum=maximum,
    )


_FADE_SCHEMA = (_field_definition("duration_ms", "integer", default=300, minimum=1, maximum=5000),)
_ZOOM_IN_SCHEMA = (
    _field_definition("from_scale", "number", default=1.0, minimum=0.1, maximum=4.0),
    _field_definition("to_scale", "number", default=1.2, minimum=0.1, maximum=4.0),
    _field_definition("duration_ms", "integer", default=1000, minimum=1, maximum=30000),
)
_ZOOM_OUT_SCHEMA = (
    _field_definition("from_scale", "number", default=1.2, minimum=0.1, maximum=4.0),
    _field_definition("to_scale", "number", default=1.0, minimum=0.1, maximum=4.0),
    _field_definition("duration_ms", "integer", default=1000, minimum=1, maximum=30000),
)
_PAN_SCHEMA = (
    _field_definition("distance", "number", default=0.1, minimum=0.0, maximum=0.5),
    _field_definition("duration_ms", "integer", default=1000, minimum=1, maximum=30000),
)

EFFECT_CATALOGUE: tuple[EffectMetadata, ...] = (
    EffectMetadata(effect_id="fade_in", name="Fade In", parameter_schema=_FADE_SCHEMA, renderable=True, deterministic=True, behavior_contract="Linear opacity from 0 to 1 over duration_ms."),
    EffectMetadata(effect_id="fade_out", name="Fade Out", parameter_schema=_FADE_SCHEMA, renderable=True, deterministic=True, behavior_contract="Linear opacity from 1 to 0 over duration_ms."),
    EffectMetadata(effect_id="zoom_in", name="Zoom In", parameter_schema=_ZOOM_IN_SCHEMA, renderable=True, deterministic=True, behavior_contract="Linear scale interpolation from from_scale to to_scale over duration_ms."),
    EffectMetadata(effect_id="zoom_out", name="Zoom Out", parameter_schema=_ZOOM_OUT_SCHEMA, renderable=True, deterministic=True, behavior_contract="Linear scale interpolation from from_scale to to_scale over duration_ms."),
    EffectMetadata(effect_id="pan_left", name="Pan Left", parameter_schema=_PAN_SCHEMA, renderable=True, deterministic=True, behavior_contract="Linear horizontal translation left by normalized distance over duration_ms."),
    EffectMetadata(effect_id="pan_right", name="Pan Right", parameter_schema=_PAN_SCHEMA, renderable=True, deterministic=True, behavior_contract="Linear horizontal translation right by normalized distance over duration_ms."),
    EffectMetadata(effect_id="pan_up", name="Pan Up", parameter_schema=_PAN_SCHEMA, renderable=True, deterministic=True, behavior_contract="Linear vertical translation up by normalized distance over duration_ms."),
    EffectMetadata(effect_id="pan_down", name="Pan Down", parameter_schema=_PAN_SCHEMA, renderable=True, deterministic=True, behavior_contract="Linear vertical translation down by normalized distance over duration_ms."),
    EffectMetadata(effect_id="brightness", name="Brightness", parameter_schema=(_field_definition("amount", "number", required=True, minimum=-1.0, maximum=1.0),), renderable=True, deterministic=True, behavior_contract="Apply a constant normalized brightness adjustment amount."),
    EffectMetadata(effect_id="contrast", name="Contrast", parameter_schema=(_field_definition("factor", "number", default=1.0, minimum=0.0, maximum=3.0),), renderable=True, deterministic=True, behavior_contract="Apply a constant contrast multiplier factor."),
    EffectMetadata(effect_id="saturation", name="Saturation", parameter_schema=(_field_definition("factor", "number", default=1.0, minimum=0.0, maximum=3.0),), renderable=True, deterministic=True, behavior_contract="Apply a constant saturation multiplier factor."),
    EffectMetadata(effect_id="blur", name="Blur", parameter_schema=(_field_definition("radius", "number", required=True, minimum=0.0, maximum=50.0),), renderable=True, deterministic=True, behavior_contract="Apply a constant blur radius in pixels."),
    EffectMetadata(effect_id="look_cinematic", name="Cinematic Look", parameter_schema=(_field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0),), renderable=True, deterministic=True, behavior_contract="Teal shadows and warm highlights with a little extra contrast; amount scales the grade."),
    EffectMetadata(effect_id="look_vintage", name="Vintage Look", parameter_schema=(_field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0),), renderable=True, deterministic=True, behavior_contract="Lifted, tinted blacks, softer whites and less saturation, like old film."),
    EffectMetadata(effect_id="look_warm", name="Warm Look", parameter_schema=(_field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0),), renderable=True, deterministic=True, behavior_contract="Lower colour temperature (golden light)."),
    EffectMetadata(effect_id="look_cool", name="Cool Look", parameter_schema=(_field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0),), renderable=True, deterministic=True, behavior_contract="Higher colour temperature (blue light)."),
    EffectMetadata(effect_id="look_punchy", name="Punchy Look", parameter_schema=(_field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0),), renderable=True, deterministic=True, behavior_contract="More contrast and saturation with light sharpening."),
    EffectMetadata(effect_id="look_mono", name="Black and White Look", parameter_schema=(_field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0),), renderable=True, deterministic=True, behavior_contract="Black and white with extra contrast."),
    EffectMetadata(effect_id="look_matte", name="Matte Look", parameter_schema=(_field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0),), renderable=True, deterministic=True, behavior_contract="Lifted blacks and softened colour, a flat film look."),
    EffectMetadata(effect_id="vignette", name="Vignette", parameter_schema=(_field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0),), renderable=True, deterministic=True, behavior_contract="Darkens the corners to draw the eye to the centre."),
    EffectMetadata(effect_id="grain", name="Film Grain", parameter_schema=(_field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0),), renderable=True, deterministic=True, behavior_contract="Moving film grain."),
    EffectMetadata(effect_id="sharpen", name="Sharpen", parameter_schema=(_field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0),), renderable=True, deterministic=True, behavior_contract="Unsharp-mask sharpening."),
    EffectMetadata(effect_id="glow", name="Glow", parameter_schema=(_field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0),), renderable=True, deterministic=True, behavior_contract="Bright areas bloom softly (screen-blended blur)."),
    EffectMetadata(effect_id="rgb_split", name="RGB Split", parameter_schema=(_field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0),), renderable=True, deterministic=True, behavior_contract="Red and blue channels pulled apart horizontally, a glitch look."),
    EffectMetadata(effect_id="flash", name="Flash", parameter_schema=(_field_definition("at_ms", "integer", default=0, minimum=0, maximum=600000), _field_definition("duration_ms", "integer", default=300, minimum=1, maximum=5000), _field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0)), renderable=True, deterministic=True, behavior_contract="The picture flashes bright at at_ms and fades back over duration_ms."),
    EffectMetadata(effect_id="shake", name="Shake", parameter_schema=(_field_definition("at_ms", "integer", default=0, minimum=0, maximum=600000), _field_definition("duration_ms", "integer", default=300, minimum=1, maximum=5000), _field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0)), renderable=True, deterministic=True, behavior_contract="The camera shakes around at_ms for duration_ms (picture slightly enlarged to hide edges)."),
    EffectMetadata(effect_id="zoom_punch", name="Zoom Punch", parameter_schema=(_field_definition("at_ms", "integer", default=0, minimum=0, maximum=600000), _field_definition("duration_ms", "integer", default=300, minimum=1, maximum=5000), _field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0)), renderable=True, deterministic=True, behavior_contract="The picture jumps closer at at_ms and settles back over duration_ms."),
    EffectMetadata(effect_id="stabilize", name="Stabilize", parameter_schema=(_field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0),), renderable=True, deterministic=True, behavior_contract="Reduces camera shake (FFmpeg deshake) and enlarges slightly to hide moving edges."),
    EffectMetadata(effect_id="light_leak", name="Light Leak", parameter_schema=(_field_definition("at_ms", "integer", default=0, minimum=0, maximum=600000), _field_definition("duration_ms", "integer", default=300, minimum=1, maximum=5000), _field_definition("amount", "number", default=0.6, minimum=0.0, maximum=1.0)), renderable=True, deterministic=True, behavior_contract="A warm glow washes over the picture at at_ms and fades over duration_ms, like light leaking into a film camera."),
)


def get_effect_metadata(effect_id: EffectId) -> EffectMetadata:
    for metadata in EFFECT_CATALOGUE:
        if metadata.effect_id == effect_id:
            return metadata
    raise KeyError(f"unknown effect id: {effect_id}")


def serialize_effect_catalogue() -> str:
    """Return stable JSON catalogue data, preserving the declared order."""
    return json.dumps(
        [item.model_dump(mode="json") for item in EFFECT_CATALOGUE],
        sort_keys=True,
        separators=(",", ":"),
    )


class AnimationRef(_Strict):
    animation_id: Literal["fade", "move", "scale", "opacity"]


class EditorClip(_Strict):
    """A stable scene/clip placement that references an asset and track."""

    clip_id: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)
    track_id: str = Field(min_length=1)
    start_tick: int = Field(ge=0)
    duration_ticks: int = Field(gt=0)
    source_in_ms: int | None = Field(default=None, ge=0)
    source_out_ms: int | None = Field(default=None, ge=0)
    transform: Transform = Field(default_factory=Transform)
    audio: AudioProperties | None = None
    transition: TransitionRef | None = None
    effects: list[EffectRef] = Field(default_factory=list)
    animations: list[AnimationRef] = Field(default_factory=list)
    score: float | None = None
    rationale: dict[str, float] = Field(default_factory=dict)


class TimelineMarker(_Strict):
    marker_id: str = Field(min_length=1)
    kind: Literal["beat", "section", "custom"]
    position_tick: int = Field(ge=0)
    label: str | None = None
    value: float | None = None


class EditorTimeline(_Strict):
    """Versioned project timeline for future multi-track editing operations.

    This model is data-only in P2.1. It is intentionally separate from the
    v0.1 ``Timeline`` still consumed by the cutter, evaluator, and renderer.
    """

    schema_version: Literal["1.0"] = "1.0"
    project_id: str = Field(default_factory=lambda: f"project_{uuid4().hex}", min_length=1)
    timeline_id: str = Field(default_factory=lambda: f"timeline_{uuid4().hex}", min_length=1)
    revision: int = Field(default=1, ge=1)
    parent_revision: int | None = Field(default=None, ge=1)
    duration_ticks: int = Field(gt=0)
    time_base: TimeBase = Field(default_factory=TimeBase)
    settings: EditorSettings = Field(default_factory=EditorSettings)
    music_asset_id: str | None = None
    music_track_id: str | None = None
    assets: list[MediaAsset] = Field(default_factory=list)
    layers: list[Layer] = Field(default_factory=list)
    tracks: list[Track] = Field(default_factory=list)
    clips: list[EditorClip] = Field(default_factory=list)
    markers: list[TimelineMarker] = Field(default_factory=list)
    provenance: Provenance | None = None

    @classmethod
    def load(cls, path: str | Path) -> "EditorTimeline":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def asset_by_id(self, asset_id: str) -> MediaAsset:
        return next(asset for asset in self.assets if asset.asset_id == asset_id)

    @property
    def duration_ms(self) -> int:
        return int(round(self.duration_ticks * 1000 / self.time_base.ticks_per_second))
