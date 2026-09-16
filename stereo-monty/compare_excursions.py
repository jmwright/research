#!/usr/bin/env python3
"""
Stage 1's gate: did the spiral leave the object, and did it cross the handle?

`NaiveScanPolicy` ignores `percept` entirely - no on-object test, no `_undo_action`
- so the mug and the glass execute the *same* spiral. That is what makes this
comparison possible without a semantic sensor: step i in one run corresponds to
step i in the other, so the steps where the mug is on-object and the glass is not
*are* the handle, measured rather than assumed.

**A saved run holds one episode, not all of them.** `CameraSM.reset()` clears
`processed_obs` at the start of every episode, and `post_epoch` writes `model.pt`
into `<output_dir>/0` on every eval epoch, overwriting the last one. So what
survives is the last episode of the last epoch and nothing else - measured, not
inferred: `sim_glass_norawobs` holds 16000 steps against an `eval_stats.csv` whose
third and final episode ran 16000. Two consequences, both of which shape how the
runs have to be set up:

- **One object per run.** Objects cycle *within* an epoch, one episode each, so a
  run over `[rig_mug, rig_glass]` saves the glass and throws the mug away.
- **One rotation per run.** Rotations advance per *epoch*, so `n_eval_epochs: 3`
  keeps only the third. Set `n_eval_epochs: 1` and put the single rotation you
  want in `rotations`, or the surviving trace is whichever one happened to be last.

Both runs must therefore be at the same rotation for step i to mean anything, so
the rotation is read back from each `eval_stats.csv` and compared rather than
trusted.

Three things this reports, in the order stage 1 asks them.

**Do excursions exist, and do they last more than one step?** Under
`InformedPolicy` all 307 excursions across three earlier runs were exactly one step
long, because that policy replays the reverse of any step that leaves the object.
A spiral does not, so its later steps should show long unbroken runs off-object.

**Does the off-object flag still track `on_object` exactly?** It did on every step
of all three earlier runs. Re-checking is free, and it is the assumption the whole
chokepoint argument rests on, so it is measured here rather than inherited.

**Do the two runs disagree anywhere?** If they never do, the spiral never reaches
the one place the mug and the glass differ, and nothing downstream of stage 1 can
work on this pair - raise `fixed_amount` or start the spiral off-centre and run it
again. Pick a rotation that shows the handle in profile before concluding anything:
the handle runs +X and the object's front is +Y, so a 90 degree turn about Y swings
it along the view axis and there is genuinely nothing to find.

Episodes end on a match, so a run that resolves early is shorter than one that does
not and only the overlap can be compared - the step counts are printed for that
reason.

Step i means the same agent pose in both runs only when the two episodes started
from the same place. That does not hold if `GetGoodView` is allowed to translate:
it moves until the object fills a set fraction of the view finder, and the mug's
silhouette is larger than the glass's, so the two agents stop at different
distances. Comparing the first sensed location is *not* a test of this - two
different objects present different surfaces from an identical pose, so it reports
an offset either way. The settings are read from each run's own `config.pt`
instead, which also catches the case where one of the pair is stale: `model.pt`
records none of them.

`processed_observations` survives `save_raw_obs: false`; `raw_observations` and its
`pixel_loc` do not, and are not needed here. The files are loaded one at a time
because a run with raw observations left on can be over a gigabyte.

Needs the tbp.monty env for torch (~/miniforge/envs/tbp.monty), not this
directory's requirements.txt.

**Identity mode, for stage 2.** `--identical` asks the opposite question: did these
two runs produce the *same* episode? Stage 2 adds `process_off_object` defaulting to
`False` and has to show that the plumbing is inert while the flag is off, which means
comparing a flag-off run against the recorded baseline and requiring no difference at
all. That comparison is exact - no tolerance - and it does not go through the
three-field reduction the excursion report uses, because a change that left the walk
untouched but perturbed a feature value would pass a reduced comparison and still have
changed the run. Every field of every `processed_observations` entry is compared, plus
`eval_stats.csv` column by column - not byte for byte, because its `time` column is
wall clock and differs between two runs that are otherwise identical.

**Depth mode, for stage 3.** `--depths` asks whether an off-object percept's location
was moved onto the object's depth plane, or left at the depth the void was filled with
- 1 m in Habitat, 10 m on the rig. Left alone it sits several times further from the
camera than the object does, so every hypothesis maps far outside its own model, the
mask is True everywhere and nothing is contradicted. That failure is silent: the run
completes and reports nothing unusual, which is why it is measured rather than assumed.
The reference distribution is in the same file, so this needs no baseline and no second
run - the on-object steps say where the surface actually is, and the off-object median
has to land inside that spread. The camera does not move during an episode
(`NaiveScanPolicy` emits rotations only, and both sensors sit at the agent's own
position with no offset), so the agent position from `config.pt` is the camera position
for every step.

**In-model mode, for stage 3.** `--in-model` reads what the learning module's probe
recorded: at each off-object step, the fraction of that object's live hypotheses whose
search location still has a model node within `max_match_distance`. Those are the
hypotheses that predicted a surface where the sensor found none - the *contradicted*
cell, and the only thing stage 4 can act on. If no object is ever in model at an
off-object step, flipping the mask changes no evidence and stage 4 does nothing, which
is worth learning from a read-only probe rather than from an evidence change.

Three numbers per object, because one fraction over every hypothesis cannot tell a
wrong-pose hypothesis being contradicted - which is the mechanism working - from a
leading one being contradicted, which is not. `all` is every hypothesis of that
object; `contenders` is those within `x_percent_threshold` of its best evidence, the
band the learning module itself treats as still in the running; `best` is its single
highest-evidence hypothesis. A high `all` with a near-zero `contenders` means the
contradicted hypotheses were ones already losing, and stage 4 would prune noise
rather than change an outcome.

The interesting number is the *difference between objects within one run*, not the
absolute level. On the glass capture the spiral crosses the region where a mug's handle
would be: a `rig_mug` hypothesis maps that location onto the handle and is contradicted,
while a `rig_glass` hypothesis predicts empty space there and is not. One observation,
opposite meanings - which is the whole reason the off-object item exists.

This also checks that the probe fired exactly once per off-object step. It is cheap and
it catches the probe sitting on a code path the off-object steps do not take, which is a
failure that otherwise looks exactly like "nothing was in model".

**Gap mode, for stage 4.** `--gap` asks *when* the evidence gap opened. Every margin
stage 4 records is read at the final off-object step, and a verdict that only separates
on its last step is fragile at any penalty size - it would have gone the other way had
the episode ended a few steps earlier, which makes the penalty look like it decided
something it merely happened to finish ahead of. The margin is between the run's final
leader and its closest rival at each step, so it is signed: negative means the eventual
winner was behind at that step. Three things come out of it.

**The settle step** is the last step at which the eventual winner was *not* ahead.
Early is robust; near the end is the fragile case, and the percentage through the
episode is what to read rather than the raw index, because the two captures have
different lengths.

**Hand-overs** count how often the lead changed at all. A margin that crosses zero
repeatedly is a different situation from one that crosses once and climbs, even when
both settle at the same step, because the first says the two objects are genuinely
close over that whole stretch.

**The decile table** shows the shape. A gap that grows steadily across the episode is
the mechanism accumulating evidence, which is what stage 4 claims; one that is flat
until the end and then jumps came from a handful of steps and should be read as such.

This reads off the same probe as `--in-model` and needs no new run. It measures
off-object steps only, because those are the only ones the probe fires on - the gap may
well move on on-object steps in between, and this cannot see that.

**Re-entry mode, for stage 5.** `--reentry` asks *where* a hypothesis space died:
inside an excursion, or at the step the sensor came back. Stage 4 clamps the slope
tracker on off-object steps by skipping `tracker.update`, so the penalty accumulates
in the learning module's evidence array while the tracker's window does not move.
Nothing is pruned during the excursion - that is the clamp working. The whole
accumulated drop then enters the buffer as a *single* diff at the first on-object
step afterwards, which is the one place a window-averaged slope cannot tell a long
slow decline from a cliff.

The probe fires on off-object steps only, so it cannot watch an on-object step
directly. It does not need to. What it can establish is whether the two off-object
steps that *bracket* a graph's disappearance are adjacent in the global step index.
If they are, nothing but off-object steps happened in between and the clamped path
did the pruning. If an on-object step sits between them, the collapse is bracketed by
re-entry and the clamped path is not what did it. That distinction is the whole
question, and it is answered from runs already on disk.

Two numbers come out of it.

**The withheld drop** is how far a graph's best evidence fell across one excursion -
the quantity the clamp keeps out of the tracker and hands over in one slot at
re-entry. It is a floor on the diff that actually lands, not the diff itself: the
on-object step contributes its own change on top, and the probe cannot see that.

**The absorbable sum** is what the rest of the window would have to contribute for a
hypothesis to survive that slot: `deletion_trigger_slope * (window_size - 1)` minus
the withheld drop, over the remaining slots. When the withheld drop alone puts this
out of reach of plausible per-step gains, the graph is deleted at re-entry whatever
else it was doing, and reporting it is how stage 5 is sized rather than guessed.

**Run it against a zero-penalty control.** A third object being pruned is ordinary
tracker behaviour and happens with the penalty off; only a collapse that appears when
the penalty is raised is the penalty's doing. The mug capture loses `rig_block` at
step 45 at 0.0, 0.01 and 1.0 alike - reading that as collateral damage would be
wrong. Pass the control run as the second argument and the report says which
disappearances are new.

**Pose mode, for the visible-handle question.** `--pose` asks the only question the
contradicted-cell fractions cannot: of the mug hypotheses whose handle the sensor
could actually see, does the mechanism eliminate them? A glass episode contradicts
almost nothing at most azimuths, but that is two different facts mixed together - a
handle tucked behind the body is unfalsifiable from one viewpoint no matter how good
the mechanism is, while a handle in plain view that survives is a defect. Separating
them needs the leading hypothesis's *pose*, which is why the probe records
`best_pose` and `best_location`.

What to look for, on a simulated glass episode:

- **The leader starts at a visible-handle azimuth and is driven round to a hidden
  one.** The mechanism works: it eliminated what it could see, and what survives is
  unfalsifiable by geometry. This is the claim worth making.
- **The leader sits at a visible-handle azimuth throughout while being
  contradicted.** It does not work - evidence is being subtracted without changing
  which pose wins.
- **The leader starts hidden and never moves.** The episode never tested the
  mechanism; re-run from a starting pose that puts the handle in view.

**The rotation convention is measured, not assumed.** Every episode here is presented
at `[0, 0, 0]`, where the true pose is the identity and `R` and its transpose are
indistinguishable - so calibrating against the known rotation proves nothing. Instead
this reads the convention off the run itself, using the displacer's own equation
(`hypotheses_displacer.py:156`): across consecutive steps holding the same leader, a
sensed displacement in the sensor frame maps to the model frame by `poses`, so
`delta_model = R @ delta_world` under one convention and `R.T @ delta_world` under the
other. Both are tried and the residuals printed. If neither is clearly better the mode
refuses to report azimuths rather than reporting them backwards, which is the failure
this guards against - a transposed rotation gives a plausible, wrong answer at every
step.

**The handle direction is measured too**, from the graph rather than from the CAD: the
body axis is the circle most nodes lie on, and the handle is the mean direction of the
nodes beyond it. Taking radius from the model origin does not work - the mug's origin
is the bounding-box centre of body plus handle, which sits about 21 mm off the body
axis, so the body's far side reads as "beyond the body" and a phantom second lobe
appears opposite the real one. The axis fit is validated against a rotationally
symmetric graph in the same library, which must come out centred on its own origin
with no nodes beyond its body.

Do not compare `model.pt` files as bytes. Pickle is not guaranteed to serialise the
same object graph identically across runs, so a hash mismatch there tells you nothing
about whether the episode changed. Compare the values, which is what this does.

Usage:

    ./compare_excursions.py <with-feature model.pt> <without-feature model.pt>
    ./compare_excursions.py --identical <baseline model.pt> <flag-off model.pt>
    ./compare_excursions.py --depths <model.pt> [<model.pt>]
    ./compare_excursions.py --in-model <model.pt> [<model.pt>]
    ./compare_excursions.py --gap <model.pt> [<model.pt>]
    ./compare_excursions.py --reentry <model.pt> [<control model.pt>]
    ./compare_excursions.py --pose <model.pt> [<model.pt>]

Conventionally the mug first and the glass second, each one a finished run's
<results>/<run_name>/0/model.pt. In identity mode the two are the same object.
"""
import csv
import gc
import pickle
import re
import sys
from pathlib import Path

import numpy as np
import torch

SENSOR = 0  # sensor_module_0 is the patch; sensor_module_1 is the view finder probe

LEARNER = 0  # learning_module_0; these experiments are all 1lm_1sm

# `use_state` was renamed in tbp.monty 0.47.0 (PR 1051). Stage 1 runs on 0.46.0 and
# stage 4 will not, so accept either rather than pinning to the checkout.
FLAG_NAMES = ("use_state", "process_features_in_lm")

# What the run recorded about itself. `<run>/0/config.pt` sits beside `model.pt` and
# holds the instantiated config, which is the only place the settings that decide
# whether two runs are comparable are written down - `model.pt` has none of them.
CONFIG_NAME = "config.pt"

# Asks whether two runs are the same episode rather than how two objects differ.
IDENTITY_FLAG = "--identical"

# Asks, of one run at a time, where its off-object locations ended up.
DEPTHS_FLAG = "--depths"

# Asks what the learning module's probe saw at those off-object steps.
IN_MODEL_FLAG = "--in-model"

# Asks *when* the evidence gap opened, rather than how large it finished.
GAP_FLAG = "--gap"

# Asks *where* a hypothesis space died - inside an excursion, or at re-entry.
REENTRY_FLAG = "--reentry"

# Asks where the leading hypothesis puts the handle, and whether that moves.
POSE_FLAG = "--pose"

# The mug and glass are both 75.0 mm across (design/mechanical/components/test/mug.py),
# so the body any hypothesis has to explain is this radius. Used to find the axis by
# vote and, with a node-spacing margin, to separate handle nodes from body nodes.
BODY_RADIUS = 0.0375
# One node spacing (the graphs run about 4 mm between neighbours). Measured on
# rig_mug, the body wall's own spread reaches 2.5 mm beyond the nominal radius while
# the handle sits 23 mm beyond it, so 3 mm separates them and 2 mm leaves about 30
# body nodes in the handle set - enough to drag the circular mean off +X.
BODY_MARGIN = 0.003

# A rotationally symmetric graph must fit at its own origin with nothing beyond its
# body. If it does not, the axis fit is not measuring what it claims and no azimuth
# below it means anything.
AXIS_TOLERANCE = 0.0025

# What the clamp's arithmetic is read against. `deletion_trigger_slope` and the
# penalty are recorded per run; the tracker's own geometry is not - `config.pt` saves
# `evidence_slope_trackers` empty, because they are built per graph at match time and
# cleared. So the window comes from the installed EvidenceSlopeTracker's defaults and
# is reported as assumed rather than read, which is the honest label: it is right for
# every run here and would be wrong for one that passed the argument.
TRACKER_DEFAULTS = {"window_size": 10, "min_age": 5}

# eval_stats.csv columns that differ between two runs of the *same* experiment and
# say nothing about whether the episode changed. `time` is wall clock: two identical
# runs of the stage 2 baseline recorded 1.875 s and 1.787 s. Comparing the file as
# bytes reports that as a difference, which is why this comparison is column-wise -
# and why the excluded columns are printed rather than dropped silently.
VOLATILE_COLUMNS = ("time",)


def read_run(path):
    """The saved episode's (on_object, off-object flag, location) per step."""
    state = torch.load(path, map_location="cpu", weights_only=False)
    steps = [step_of(step) for step in state["sm_dict"][SENSOR]["processed_observations"]]

    del state
    gc.collect()

    return steps


def step_of(step):
    features = step.get("morphological_features") or {}
    location = step.get("location")

    return (
        bool(features.get("on_object", False)),
        flag_of(step),
        None if location is None else np.asarray(location, dtype=float),
    )


def flag_of(step):
    for name in FLAG_NAMES:
        if name in step:
            return bool(step[name])

    return None


def read_episode_label(path):
    """The object and rotation of the saved episode, from the run's last CSV row."""
    stats = path.parent.parent / "eval_stats.csv"

    if not stats.exists():
        return None, None

    rows = list(csv.DictReader(stats.open()))

    if not rows:
        return None, None

    last = rows[-1]

    return (
        last.get("primary_target_object"),
        last.get("primary_target_rotation_euler"),
        numbers_in(last.get("primary_target_position")),
    )


def numbers_in(text):
    """The floats in a numpy-printed vector like "[0.  1.5 0. ]"."""
    if not text:
        return None

    found = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text)

    return np.asarray([float(value) for value in found], dtype=float) if found else None


def runs_of(values, wanted):
    """Lengths of every maximal run of `wanted`."""
    lengths, current = [], 0

    for value in values:
        if value == wanted:
            current += 1
        elif current:
            lengths.append(current)
            current = 0

    if current:
        lengths.append(current)

    return lengths


def describe(label, steps):
    """Prints one run's excursion behaviour and returns whether it left the object."""
    on_object = [step[0] for step in steps]
    excursions = runs_of(on_object, False)
    tracking = [step[1] for step in steps if step[1] is not None]

    fraction = 100 * sum(on_object) / len(on_object) if on_object else 0.0
    longest = max(excursions) if excursions else 0

    print(f" {label:22} {len(on_object):6d} steps, {fraction:5.1f}% on object, "
          f"{len(excursions):4d} excursions, longest {longest}")

    if tracking and any(flag != state for flag, state in zip(tracking, on_object)):
        differing = sum(1 for f, s in zip(tracking, on_object) if f != s)
        print(f" {'':22} the off-object flag and on_object disagree on "
              f"{differing} steps - the chokepoint has moved, re-read it")

    return longest > 1


def read_run_config(path):
    """The settings from a run's own config.pt that decide comparability."""
    stored = path.parent / CONFIG_NAME

    if not stored.exists():
        return None

    try:
        config = torch.load(stored, map_location="cpu", weights_only=False)
    except (OSError, EOFError, RuntimeError, pickle.UnpicklingError):
        # A truncated or foreign config.pt is a missing check, not a crash - the
        # comparison is still worth running with the settings unverified. torch
        # reports a corrupt archive as a bare RuntimeError ("PytorchStreamReader
        # failed reading zip archive"), which is why that broad one is here; the
        # errors that mean a broken checkout are caught separately below.
        return None
    except (ImportError, SyntaxError, AttributeError) as unreadable:
        # These mean the tbp.monty checkout that has to interpret the pickle is
        # broken, not that the run failed to record anything. Swallowing them
        # reports "settings unchecked" and lets every other number through, which
        # cost a debugging session once: a SyntaxError mid-edit surfaced here as a
        # missing agent position. Say what actually happened and stop.
        raise SystemExit(
            f" ! cannot read {stored} because the tbp.monty checkout does not "
            f"import: {type(unreadable).__name__}: {unreadable}"
        ) from unreadable

    procedures = config.get("eval_env_interface_args", {}).get(
        "positioning_procedures"
    ) or []
    agent = (
        config.get("environment", {})
        .get("env_init_args", {})
        .get("agents", {})
        .get("agent_args", {})
    )

    return {
        "translates": any(
            getattr(procedure, "_allow_translation", False) for procedure in procedures
        ),
        "match_count": getattr(config.get("match_criterion"), "_count", None),
        "position": agent.get("position"),
        "max_total_steps": config.get("max_total_steps"),
    }


def check_comparable(configs, names):
    """Prints why two runs may not be comparable step by step. True if they are."""
    if any(config is None for config in configs):
        print(f" {'':22} no config.pt beside a model.pt - settings unchecked")
        return True

    comparable = True

    # GetGoodView translates until the object fills a set fraction of the view, and
    # the mug's silhouette is larger than the glass's, so with translation on the two
    # agents stop at different distances and step i is not the same pose in both.
    if any(config["translates"] for config in configs):
        print(f" {'':22} ! positioning translated the agent, so the two spirals are "
              f"anchored differently - read the object-frame section below, not the "
              f"step indices")
        comparable = False

    for key, description in (
        ("match_count", "match criterion"),
        ("position", "agent position"),
        ("max_total_steps", "step cap"),
    ):
        values = [config[key] for config in configs]

        if values[0] != values[1]:
            print(f" {'':22} ! {description} differs between the runs "
                  f"({values[0]} vs {values[1]}) - one of them is stale, re-run it")
            comparable = False

    return comparable


def cloud(steps, position):
    """On-object locations relative to the object centre, in millimetres."""
    return np.array([
        1000.0 * (step[2] - position)
        for step in steps if step[0] and step[2] is not None
    ])


def distances_from(steps, camera, on_object):
    """Distances in mm from the camera to the sensed location of matching steps."""
    return np.array([
        1000.0 * np.linalg.norm(step[2] - camera)
        for step in steps if step[0] == on_object and step[2] is not None
    ])


def extent(points):
    return " x ".join(
        f"{low:+7.1f}..{high:+7.1f}"
        for low, high in zip(points.min(axis=0), points.max(axis=0))
    )


def where_they_differ(runs, names, disagreement, position):
    """Prints where the disagreeing steps sit relative to each object's surface.

    The step index alignment is only as good as the two episodes starting from the
    same pose, which `GetGoodView` does not guarantee. World coordinates do not care:
    both objects are placed at the same position and rotation, so a disagreeing step
    that lands beyond the second object's surface is on geometry the first object has
    and the second does not - the handle - regardless of where each walk began.
    """
    body = cloud(runs[1], position)
    differing = np.array([
        1000.0 * (runs[0][index][2] - position)
        for index in disagreement if runs[0][index][2] is not None
    ])

    if not len(body) or not len(differing):
        print(f" {'':22} no locations to compare in object coordinates")
        return

    print()
    print(f" {names[0] + ' surface':22} {extent(cloud(runs[0], position))}  mm (x, y, z)")
    print(f" {names[1] + ' surface':22} {extent(body)}  mm")
    print(f" {'differing steps':22} {extent(differing)}  mm")

    # Distance from each differing point to the nearest point the second object
    # actually presented. Small means the two walks merely grazed different sides of
    # the same surface; large means the point is off that object entirely.
    gaps = np.linalg.norm(differing[:, None, :] - body[None, :, :], axis=2).min(axis=1)

    print(f" {'':22} distance to nearest {names[1]} surface point: "
          f"median {np.median(gaps):.1f} mm, max {gaps.max():.1f} mm")

    axis = int(np.argmax(np.abs(differing.mean(axis=0) - body.mean(axis=0))))
    beyond = (
        differing[:, axis].max() - body[:, axis].max()
        if differing[:, axis].mean() > body[:, axis].mean()
        else body[:, axis].min() - differing[:, axis].min()
    )

    print(f" {'':22} they sit {beyond:+.1f} mm beyond the {'xyz'[axis]} extent of "
          f"{names[1]}, centroid offset {np.linalg.norm(differing.mean(axis=0) - body.mean(axis=0)):.1f} mm")


def read_full_steps(path):
    """Every recorded field of the saved episode's per-step observations.

    `read_run` keeps only the three things the excursion report asks about. An
    identity check cannot afford that reduction, so this keeps the dicts whole.
    """
    state = torch.load(path, map_location="cpu", weights_only=False)
    steps = state["sm_dict"][SENSOR]["processed_observations"]

    del state
    gc.collect()

    return steps


def read_in_model(path):
    """The probe's per-step in-model fractions, keyed by graph id, or None.

    `lm_dict` holds no hypothesis or evidence trace of its own, so this is carried
    there deliberately by `EvidenceGraphLM.state_dict`. A run made before that
    existed has no key at all, which is why the absence is reported rather than
    read as an empty result.
    """
    state = torch.load(path, map_location="cpu", weights_only=False)
    logged = state["lm_dict"][LEARNER].get("off_object_in_model")

    del state
    gc.collect()

    return logged


def same_value(left, right):
    """Exact equality, structure included. No tolerance: this is an identity check."""
    if type(left) is not type(right):
        return False

    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            same_value(left[key], right[key]) for key in left
        )

    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(
            same_value(one, other) for one, other in zip(left, right)
        )

    if isinstance(left, np.ndarray):
        if left.shape != right.shape or left.dtype != right.dtype:
            return False

        # An off-object step can carry a NaN location, and NaN != NaN would report
        # every such step as a difference. Two runs that both produced NaN there are
        # identical for this purpose. equal_nan is only legal on floating dtypes.
        floating = np.issubdtype(left.dtype, np.floating)

        return np.array_equal(left, right, equal_nan=floating)

    if isinstance(left, float):
        return left == right or (np.isnan(left) and np.isnan(right))

    return bool(left == right)


def first_difference(left, right, where):
    """A readable path to the first field that differs, or None if none does."""
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) ^ set(right)):
            return f"{where}.{key} (present in only one run)"

        for key in sorted(left):
            found = first_difference(left[key], right[key], f"{where}.{key}")

            if found:
                return found

        return None

    return None if same_value(left, right) else where


def eval_stats_difference(paths):
    """Which eval_stats.csv columns differ between two runs.

    Empty when they agree, None when a file is missing. Wall-clock columns are
    excluded - see VOLATILE_COLUMNS.
    """
    files = [path.parent.parent / "eval_stats.csv" for path in paths]

    if not all(stats.exists() for stats in files):
        return None

    rows = [list(csv.DictReader(stats.open())) for stats in files]

    if len(rows[0]) != len(rows[1]):
        return [f"row count ({len(rows[0])} and {len(rows[1])})"]

    differing = []

    for left, right in zip(*rows):
        if left.keys() != right.keys():
            return ["the columns themselves"]

        for column in left:
            if column in VOLATILE_COLUMNS or column in differing:
                continue

            if left[column] != right[column]:
                differing.append(column)

    return differing


def identity_report(paths, names, labels):
    """Whether two runs of the same experiment produced the same episode.

    Stage 2 adds `process_off_object` defaulting to False and has to prove the flag is
    inert while off. The proof is that the flag-off run reproduces the baseline
    exactly, so anything short of "no differences" is a failure, and the first
    differing field is printed because that is what says where to look.
    """
    targets = [target for target, _, _ in labels]

    if all(targets) and targets[0] != targets[1]:
        print(f" ! these runs saved different objects ({targets[0]} and "
              f"{targets[1]}), so they were never going to match. Identity mode "
              f"compares two runs of the same experiment.")
        return 1

    if not check_comparable([read_run_config(path) for path in paths], names):
        print(f" {'':22} the settings differ, so a difference below is that, not "
              f"the flag")

    runs = [read_full_steps(path) for path in paths]

    print()

    if len(runs[0]) != len(runs[1]):
        print(f" ! step counts differ: {len(runs[0])} and {len(runs[1])}. The "
              f"episode ended somewhere else, so the flag was not inert.")
        return 1

    differences = [
        (index, first_difference(runs[0][index], runs[1][index], f"step {index}"))
        for index in range(len(runs[0]))
        if not same_value(runs[0][index], runs[1][index])
    ]

    stats_differ = eval_stats_difference(paths)

    print(f" {'steps':22} {len(runs[0])} in both")
    print(f" {'processed obs':22} "
          f"{'identical' if not differences else str(len(differences)) + ' steps differ'}")

    if stats_differ is None:
        print(f" {'eval_stats.csv':22} missing from one run - not checked")
    elif stats_differ:
        print(f" {'eval_stats.csv':22} differs in {', '.join(stats_differ)}")
    else:
        print(f" {'eval_stats.csv':22} identical, ignoring "
              f"{', '.join(VOLATILE_COLUMNS)}")

    if differences:
        print()
        for index, where in differences[:5]:
            print(f" {'':22} first difference at {where}")

        if len(differences) > 5:
            print(f" {'':22} ... and {len(differences) - 5} more differing steps")

    print()

    if differences or stats_differ:
        print(" ! the two runs are not identical, so the change is not inert. With "
              "the flag off this is a bug in the plumbing; fix it before measuring "
              "anything with the flag on.")
        return 1

    print(" the two runs are identical, so the plumbing is inert with the flag off "
          "and stage 2 is passed.")

    return 0


def depths_report(paths, names):
    """Whether off-object locations sit on the object's depth plane or at the void's.

    Passing means the off-object median falls inside the on-object spread. The
    criterion is taken from the run's own on-object steps rather than a constant,
    so it carries from the 1 m Habitat void to the rig's 10 m one unchanged.

    Failing is the expected result with the substitution off - that run is the
    *before* measurement, and the ratio it prints is what the substitution has to
    remove. Failing with the substitution on means the parameter never reached the
    sensor module, and everything measured downstream of it is meaningless.
    """
    failures = 0

    for path, name in zip(paths, names):
        config = read_run_config(path)
        camera = None if config is None else config.get("position")

        if camera is None:
            print()
            print(f" {name:22} no agent position in config.pt, so the camera cannot "
                  f"be placed - settings unchecked")
            failures += 1
            continue

        camera = np.asarray(camera, dtype=float)
        steps = read_run(path)
        on = distances_from(steps, camera, True)
        off = distances_from(steps, camera, False)

        print()

        if not len(on) or not len(off):
            print(f" {name:22} needs both on- and off-object steps to compare; this "
                  f"run has {len(on)} and {len(off)}")
            failures += 1
            continue

        print(f" {name:22} on object  {np.median(on):8.1f} mm median, "
              f"{on.min():7.1f} .. {on.max():7.1f} over {len(on):4d} steps")
        print(f" {'':22} off object {np.median(off):8.1f} mm median, "
              f"{off.min():7.1f} .. {off.max():7.1f} over {len(off):4d} steps")

        inside = on.min() <= np.median(off) <= on.max()

        print(f" {'':22} off/on median ratio {np.median(off) / np.median(on):.2f} - "
              f"{'inside' if inside else 'OUTSIDE'} the on-object spread")

        if not inside:
            failures += 1

    print()

    if failures:
        print(" ! off-object locations do not sit on the object's depth plane. With "
              "substitute_off_object_depth off this is the expected before "
              "measurement; with it on, the location is still the void's and nothing "
              "measured from it means anything - check the parameter reached "
              "sensor_module_0 by composing the config, not by reading it.")
        return 1

    print(" off-object locations sit within the on-object depth spread, so the void "
          "substitution took and the search locations are worth scoring.")

    return 0


def percent_nonzero(values):
    """How often a per-step series was anything other than zero."""
    return 100.0 * np.count_nonzero(values) / len(values)


def probe_series(path, name):
    """The probe's log for one run, or None with the reason already printed.

    Both `--in-model` and `--gap` need the same three failures named the same way -
    no probe, an empty probe, and the older single-fraction shape - so the checks
    live here rather than in each report.
    """
    logged = read_in_model(path)

    if logged is None:
        print(f" {name:22} no off_object_in_model in lm_dict - this run predates "
              f"the probe, or its learning module is not an EvidenceGraphLM")
        return None

    if not logged:
        print(f" {name:22} the probe logged nothing - process_off_object was off, or "
              f"the probe is not on the path off-object steps take")
        return None

    if not isinstance(next(iter(logged[0].values()), None), dict):
        print(f" {name:22} this run carries the single-fraction probe, which records "
              f"no evidence - re-run it against the three-number one")
        return None

    return logged


def gap_report(paths, names):
    """When the evidence gap opened, per run.

    The margin is the final leader's best evidence minus its closest rival's, at
    every off-object step, so it is signed and a negative value means the eventual
    winner was behind. What matters is the *settle step* - the last step at which it
    was behind - because a verdict that only separates at the end is fragile at any
    penalty, and reading the final margin alone cannot tell the two apart.
    """
    failures = 0

    for path, name in zip(paths, names):
        print()

        logged = probe_series(path, name)

        if logged is None:
            failures += 1
            continue

        steps = len(logged)

        leaders = [
            max(entry, key=lambda graph: entry[graph]["best_evidence"])
            for entry in logged
        ]
        winner = leaders[-1]

        # Signed against the *final* leader rather than against whoever leads at each
        # step, which is what makes a negative value meaningful: it says the object
        # that eventually won was losing here. A margin taken against the step's own
        # leader is non-negative by construction and would hide exactly that.
        margins = np.full(steps, np.nan)

        for index, entry in enumerate(logged):
            if winner not in entry:
                # The winner's own space was emptied at this step. Leaving it NaN
                # keeps it out of the settle test rather than scoring it as a loss,
                # because no comparison was possible - it is reported separately.
                continue

            rivals = [
                value["best_evidence"]
                for graph, value in entry.items()
                if graph != winner
            ]

            if rivals:
                margins[index] = entry[winner]["best_evidence"] - max(rivals)

        absent = int(np.isnan(margins).sum())
        ahead = margins > 0

        # The last step it was not ahead. Everything after this point is decided, so
        # this is the step the verdict actually dates from.
        behind = np.where(~ahead & ~np.isnan(margins))[0]
        settle = int(behind[-1]) + 1 if len(behind) else 0

        handovers = sum(1 for one, other in zip(leaders, leaders[1:]) if one != other)

        print(f" {name:22} {steps} off-object steps, final leader {winner} "
              f"by {margins[-1]:+.4f}")

        if absent:
            print(f" {'':22}   ! {winner} had no hypotheses on {absent} of those "
                  f"steps, which are excluded from the settle test")

        if settle == 0:
            print(f" {'':22}   led from the first off-object step - the gap was "
                  f"already open before the excursion began")
        else:
            print(f" {'':22}   settled at step {settle}/{steps} "
                  f"({100.0 * settle / steps:.1f}% through), behind on "
                  f"{int((~ahead & ~np.isnan(margins)).sum())} steps")

        print(f" {'':22}   lead changed hands {handovers} times")

        # Deciles rather than every step: the shape is the question, and 266 lines of
        # it is not readable. Each column is the margin at that fraction of the way
        # through the off-object steps.
        points = [max(0, round(fraction * steps / 10) - 1) for fraction in range(1, 11)]
        print(f" {'':22}   margin at deciles: "
              + "  ".join(f"{margins[point]:+.3f}" for point in points))

        final = margins[-1]

        if np.isfinite(final) and final > 0:
            # Where it last reached half its final size and stayed there. A gap that
            # only gets there in its last tenth was decided by a handful of steps
            # whatever its final value looks like.
            below = np.where(~(margins >= final / 2) & ~np.isnan(margins))[0]
            half = int(below[-1]) + 1 if len(below) else 0
            print(f" {'':22}   reached half its final margin at step {half}/{steps} "
                  f"({100.0 * half / steps:.1f}% through)")

            if half > 0.75 * steps:
                print(f" {'':22}   ! most of this margin arrived in the last quarter "
                      f"of the episode, so it rests on few steps - read the verdict "
                      f"as fragile at this penalty")
                failures += 1

    print()

    if failures:
        print(" ! at least one run could not be read, or its gap opened too late to "
              "call the verdict settled. A margin that arrives in the last quarter of "
              "the episode would have gone the other way had the spiral stopped "
              "earlier, which is a property of the run length and not of the objects.")
        return 1

    print(" the gap was open well before the episode ended on every run read, so the "
          "verdict does not depend on where the spiral happened to stop. This sees "
          "off-object steps only - the margin can also move on the on-object steps "
          "between them.")

    return 0


def in_model_report(paths, names):
    """What the probe recorded at off-object steps, per object in memory.

    Reports `all`, `contenders` and `best` per object, and how often the step's
    *leading* object - the one with the highest evidence at that step - had its own
    best hypothesis contradicted. That last number is the one that says whether the
    mechanism would change an outcome rather than prune hypotheses already losing.

    A run where every object is flat zero is the stage 4 null result: nothing was
    contradicted anywhere the sensor went, so the mask has nothing to flip.
    """
    failures = 0

    for path, name in zip(paths, names):
        logged = read_in_model(path)
        off_steps = sum(1 for step in read_run(path) if not step[0])

        print()

        if logged is None:
            print(f" {name:22} no off_object_in_model in lm_dict - this run predates "
                  f"the probe, or its learning module is not an EvidenceGraphLM")
            failures += 1
            continue

        if not logged:
            print(f" {name:22} the probe logged nothing across {off_steps} off-object "
                  f"steps - process_off_object was off, or the probe is not on the "
                  f"path those steps take")
            failures += 1
            continue

        if len(logged) != off_steps:
            print(f" {name:22} ! the probe fired {len(logged)} times over {off_steps} "
                  f"off-object steps - it is not running once per off-object step, so "
                  f"the numbers below are about some other set of steps")
            failures += 1

        # The single-fraction probe stored a float per object; the three-number one
        # stores a dict. Reading the old shape with the new code would index a float
        # by a string, so it is named rather than left to fail somewhere less obvious.
        if not isinstance(next(iter(logged[0].values()), None), dict):
            print(f" {name:22} this run carries the single-fraction probe, which "
                  f"cannot answer which hypotheses were contradicted - re-run it "
                  f"against the three-number one")
            failures += 1
            continue

        graphs = sorted({graph for entry in logged for graph in entry})

        print(f" {name:22} {len(logged)} off-object steps logged, "
              f"{len(graphs)} objects in memory")

        series = {}

        for graph in graphs:
            entries = [entry[graph] for entry in logged if graph in entry]
            values = {
                key: np.array([entry[key] for entry in entries], dtype=float)
                for key in ("all", "contenders", "best", "best_evidence")
            }
            series[graph] = values

            # Every percentage on this row is over the steps where this object had
            # any hypotheses at all, which is not the same number for every object
            # and not always the episode length - so the denominator is printed
            # rather than left to be assumed. An object absent from a step had its
            # hypothesis space emptied, which is a finding and not a gap in the log.
            print(f" {'':22}   {graph:12} on {len(entries):4d}/{len(logged)} steps: "
                  f"all {np.median(values['all']):5.3f} on "
                  f"{percent_nonzero(values['all']):5.1f}%   "
                  f"contenders {np.median(values['contenders']):5.3f} on "
                  f"{percent_nonzero(values['contenders']):5.1f}%   "
                  f"best on {100.0 * values['best'].mean():5.1f}%   "
                  f"({int(np.median([e['contender_count'] for e in entries]))} "
                  f"contenders)")

            if len(entries) < len(logged):
                print(f" {'':22}   {'':12} ! {graph} had no hypotheses on "
                      f"{len(logged) - len(entries)} of those steps - its space was "
                      f"emptied, so its percentages are over the rest")

        # At each step the leading object is the one with the highest evidence. How
        # often *its* best hypothesis was contradicted is the number that decides
        # whether flipping the mask could move the verdict, rather than only thin out
        # hypotheses that were already losing.
        leaders = [
            max(entry, key=lambda graph: entry[graph]["best_evidence"])
            for entry in logged
        ]
        hits = sum(entry[leader]["best"] > 0 for entry, leader in zip(logged, leaders))
        share = ", ".join(
            f"{graph} {100.0 * leaders.count(graph) / len(leaders):.0f}%"
            for graph in sorted(set(leaders), key=leaders.count, reverse=True)
        )

        print(f" {'':22} leading hypothesis contradicted on "
              f"{100.0 * hits / len(logged):.1f}% of {len(logged)} steps "
              f"(leader was {share})")

        if max(values["all"].max() for values in series.values()) == 0.0:
            print(f" {'':22} nothing was ever in model here, so on this capture "
                  f"flipping the mask would change no evidence")
            failures += 1
        elif len(series) > 1:
            ranked = sorted(
                series,
                key=lambda graph: percent_nonzero(series[graph]["contenders"]),
                reverse=True,
            )
            print(f" {'':22} contenders contradicted most often: {ranked[0]} "
                  f"({percent_nonzero(series[ranked[0]]['contenders']):.1f}% of "
                  f"steps), least: {ranked[-1]} "
                  f"({percent_nonzero(series[ranked[-1]]['contenders']):.1f}%)")

    print()

    if failures:
        print(" ! the probe did not produce a usable measurement. Check that "
              "process_off_object reached learning_module_0 by composing the config, "
              "and that the probe sits in matching_step rather than exploratory_step "
              "- an off-object step takes the location-only branch of whichever one "
              "runs.")
        return 1

    print(" the probe measured the contradicted cell at every off-object step. Read "
          "the per-object difference within each run, not the absolute level: stage 4 "
          "has something to act on only where one object is in model and another is "
          "not at the same location.")

    return 0


def read_clamp_settings(path):
    """What a run recorded about the penalty and the tracker it feeds.

    Returns None with nothing printed when the config cannot be read; the caller
    reports the arithmetic as unchecked rather than guessing at defaults, because a
    withheld drop is only meaningful against the threshold it was measured for.
    """
    stored = path.parent / CONFIG_NAME

    if not stored.exists():
        return None

    try:
        config = torch.load(stored, map_location="cpu", weights_only=False)
    except (OSError, EOFError, RuntimeError, pickle.UnpicklingError):
        return None

    # These are live objects rather than a nested dict - `learning_module_0` is an
    # instantiated EvidenceGraphLM - so this is getattr the whole way down, and every
    # step of it has to tolerate a run whose updater is the default one.
    modules = (config.get("monty_config") or {}).get("learning_modules") or {}
    learner = modules.get(f"learning_module_{LEARNER}")

    if learner is None:
        return None

    updater = getattr(learner, "hypotheses_updater", None)
    displacer = getattr(updater, "_hypotheses_displacer", None)

    return {
        "updater": type(updater).__name__ if updater is not None else None,
        "deletion_trigger_slope": getattr(updater, "deletion_trigger_slope", None),
        "off_object_contradiction": getattr(
            displacer, "off_object_contradiction", None
        ),
        "process_off_object": getattr(learner, "process_off_object", None),
    }


def excursion_spans(on_object):
    """(first, last) global step index of every maximal off-object run."""
    spans, start = [], None

    for index, on in enumerate(on_object):
        if not on and start is None:
            start = index
        elif on and start is not None:
            spans.append((start, index - 1))
            start = None

    if start is not None:
        spans.append((start, len(on_object) - 1))

    return spans


def probe_against_steps(path, name):
    """The probe's log paired with the global step index of each entry, or None.

    The pairing is the load-bearing part of this mode and it is checked rather than
    assumed: the probe appends one entry per off-object step, so if the counts ever
    disagree the mapping is silently off by however many steps were missed and every
    bracketing verdict below it is wrong.
    """
    logged = probe_series(path, name)

    if logged is None:
        return None, None, None

    on_object = [step[0] for step in read_run(path)]
    off_steps = [index for index, on in enumerate(on_object) if not on]

    if len(logged) != len(off_steps):
        print(f" {name:22} ! the probe fired {len(logged)} times over "
              f"{len(off_steps)} off-object steps, so its entries cannot be placed "
              f"on the step index - the bracketing test needs exactly one per step")
        return None, None, None

    return logged, off_steps, on_object


def disappearance_of(logged, off_steps, on_object, graph):
    """Where `graph` left the probe's log, or None if it never did.

    Returns the bracketing pair and what sits between them, which is the verdict.
    """
    present = [graph in entry for entry in logged]

    if all(present):
        return None

    seen = [index for index, is_present in enumerate(present) if is_present]

    if not seen:
        return {"never": True, "count": 0, "total": len(present)}

    last_in = seen[-1]
    first_out = next(
        index for index in range(last_in + 1, len(present)) if not present[index]
    )

    step_in, step_out = off_steps[last_in], off_steps[first_out]
    between = range(step_in + 1, step_out)
    on_between = [index for index in between if on_object[index]]

    # A graph that comes back was re-seeded rather than deleted, which is a different
    # event and would make the "collapse" reading of the gap wrong.
    returned = next(
        (off_steps[index] for index in range(first_out, len(present))
         if present[index]),
        None,
    )

    return {
        "never": False,
        "count": len(seen),
        "total": len(present),
        "step_in": step_in,
        "step_out": step_out,
        "on_between": on_between,
        "returned": returned,
    }


def withheld_drops(logged, off_steps, spans, graph):
    """Per excursion, how far `graph`'s best evidence fell while the clamp held.

    This is the quantity the clamp keeps out of the tracker: `tracker.update` is
    skipped on every off-object step, so none of this reaches the sliding window
    until the first on-object step afterwards, where all of it arrives at once.

    It is a floor on the diff that lands, not the diff. The tracker's previous sample
    was taken at the on-object step *before* the excursion and its next at the one
    after, and the probe sees neither, so the real diff is this plus whatever those
    two steps contributed. Reported as a floor for that reason.
    """
    by_step = {step: entry for step, entry in zip(off_steps, logged)}
    drops = []

    for first, last in spans:
        values = [
            by_step[step][graph]["best_evidence"]
            for step in range(first, last + 1)
            if step in by_step and graph in by_step[step]
        ]

        if len(values) < 2:
            continue

        drops.append((first, last, last - first + 1, values[-1] - values[0]))

    return drops


def reentry_report(paths, names):
    """Stage 5's gate: did anything die at re-entry rather than during an excursion?

    With one run this reports the bracketing and the withheld drops. With two, the
    second is read as a zero-penalty control and only the disappearances that are
    *new* in the first are attributed to the penalty - a third object being pruned is
    ordinary tracker behaviour and happens with the penalty off.
    """
    window = TRACKER_DEFAULTS["window_size"]
    reports, failures = [], 0

    for path, name in zip(paths, names):
        print()

        logged, off_steps, on_object = probe_against_steps(path, name)

        if logged is None:
            failures += 1
            reports.append(None)
            continue

        settings = read_clamp_settings(path)
        spans = excursion_spans(on_object)

        if settings is None:
            print(f" {name:22} no readable config.pt - the penalty and the deletion "
                  f"threshold are unchecked, so the drops below have no threshold "
                  f"to be read against")
        else:
            print(f" {name:22} {settings['updater']}, penalty "
                  f"{settings['off_object_contradiction']}, deletion slope "
                  f"{settings['deletion_trigger_slope']}, window {window} (assumed)")

        print(f" {'':22} {len(on_object)} steps, {len(spans)} excursions, longest "
              f"{max((b - a + 1) for a, b in spans) if spans else 0}")

        graphs = sorted({graph for entry in logged for graph in entry})
        found = {}

        for graph in graphs:
            gone = disappearance_of(logged, off_steps, on_object, graph)
            found[graph] = gone

            if gone is None:
                print(f" {'':22} {graph:12} present at all {len(logged)} off-object "
                      f"steps")
                continue

            if gone["never"]:
                print(f" {'':22} {graph:12} absent at every off-object step")
                continue

            print(f" {'':22} {graph:12} present at {gone['count']}/{gone['total']} "
                  f"off-object steps")
            print(f" {'':22} {'':12} last seen at step {gone['step_in']}, gone by "
                  f"step {gone['step_out']}")

            if gone["on_between"]:
                first, last = gone["on_between"][0], gone["on_between"][-1]
                print(f" {'':22} {'':12} -> {len(gone['on_between'])} on-object "
                      f"steps in between ({first}-{last}): bracketed by RE-ENTRY")
            else:
                print(f" {'':22} {'':12} -> consecutive off-object steps: died "
                      f"INSIDE the excursion")

            if gone["returned"] is not None:
                print(f" {'':22} {'':12} ! re-appears at step {gone['returned']} - "
                      f"re-seeded, not deleted")

        slope = (settings or {}).get("deletion_trigger_slope")

        for graph in graphs:
            drops = [
                entry for entry in withheld_drops(logged, off_steps, spans, graph)
                if entry[3] < 0
            ]

            if not drops:
                continue

            worst = min(drops, key=lambda entry: entry[3])
            print(f" {'':22} {graph:12} withheld drops (floor): " + ", ".join(
                f"steps {first}-{last} ({length}) {drop:+.3f}"
                for first, last, length, drop in drops[:4]
            ) + ("..." if len(drops) > 4 else ""))

            if slope is None or window < 3:
                continue

            # What the other slots would have to average for the window to clear the
            # deletion threshold once this drop occupies one of them.
            needed = (slope * (window - 1) - worst[3]) / (window - 2)
            print(f" {'':22} {'':12} worst {worst[3]:+.3f} over {worst[2]} steps "
                  f"needs the other {window - 2} slots to average {needed:+.3f} to "
                  f"survive it")

        reports.append(found)

    print()

    if failures:
        print(" ! the probe did not produce a usable measurement in every run, so "
              "the bracketing test did not run. This mode needs the three-number "
              "probe and process_off_object on - a run made before stage 3 has "
              "neither.")
        return 1

    return reentry_verdict(reports, names)


def reentry_verdict(reports, names):
    """What the bracketing adds up to, with the second run read as the control."""
    subject = reports[0]
    control = reports[1] if len(reports) > 1 else None

    died = {
        graph: gone for graph, gone in subject.items()
        if gone is not None and not gone["never"]
    }

    if control is not None:
        baseline = {
            graph for graph, gone in control.items()
            if gone is not None and not gone["never"]
        }
        shared = sorted(set(died) & baseline)

        if shared:
            print(f" {', '.join(shared)} also disappears in {names[1]}, so that is "
                  f"ordinary tracker pruning rather than the penalty's doing. Do not "
                  f"read it as collateral damage.")

        died = {graph: gone for graph, gone in died.items() if graph not in baseline}

        if not died:
            print(" nothing disappears here that does not also disappear in the "
                  "control, so this run prunes no hypothesis space the penalty is "
                  "responsible for.")
            return 0

    if not died:
        print(" every graph survives to the last off-object step, so nothing was "
              "pruned and the clamp has nothing to answer for on this run.")
        return 0

    for graph, gone in sorted(died.items()):
        where = "at re-entry" if gone["on_between"] else "inside an excursion"
        print(f" {graph} died {where} (step {gone['step_in']} -> {gone['step_out']})")

    print()

    if any(gone["on_between"] for gone in died.values()):
        print(" ! a hypothesis space died at re-entry. The clamp held during the "
              "excursion and handed the whole accumulated drop to the tracker in one "
              "slot, which is the failure stage 5 exists to fix - the withheld drop "
              "above is the size of it. Rebase the tracker's window by the penalty "
              "accumulated during the excursion before the clamp lifts; letting the "
              "clamp expire instead spreads the same drop over a few slots and "
              "prunes anyway.")
        return 1

    print(" ! a hypothesis space died inside an excursion, which the stage 4 clamp "
          "is supposed to make impossible. Check that the guard covers both "
          "tracker.update and the separate select_hypotheses call in _sample_count - "
          "guarding only the first still deletes.")
    return 1


def read_graphs(path):
    """Node positions per graph id, from the run's own learned models."""
    state = torch.load(path, map_location="cpu", weights_only=False)
    memory = state["lm_dict"][LEARNER]["graph_memory"]
    graphs = {
        graph_id: np.asarray(memory[graph_id]["patch"].pos, dtype=float)
        for graph_id in memory
    }

    del state
    gc.collect()

    return graphs


def fit_body_axis(nodes):
    """The vertical axis of the 75 mm body, as (x, z) in the model's own frame.

    Found by vote rather than least squares: a circle fitted through a height band
    is dragged inwards by the interior cavity and the dowel boss, which returned
    31.7 mm for a 37.5 mm body on the first attempt. Counting nodes that land in a
    thin shell at the known radius ignores both, because interior points do not vote.

    Returns:
        The (x, z) centre, and how many nodes lie on the shell around it.
    """
    best, centre = -1, (0.0, 0.0)

    for x in np.arange(-0.05, 0.0501, 0.0005):
        for z in np.arange(-0.05, 0.0501, 0.0005):
            radii = np.hypot(nodes[:, 0] - x, nodes[:, 2] - z)
            votes = int(np.count_nonzero(np.abs(radii - BODY_RADIUS) < 0.003))
            if votes > best:
                best, centre = votes, (x, z)

    return centre, best


def is_cylinder(nodes, centre):
    """Whether a graph's body shell about `centre` is populated at every azimuth.

    Returns:
        True if all AZIMUTH_BINS bins contain a node on the body shell.
    """
    radii = np.hypot(nodes[:, 0] - centre[0], nodes[:, 2] - centre[1])
    shell = np.abs(radii - BODY_RADIUS) < 0.003

    if not shell.any():
        return False

    azimuth = np.degrees(
        np.arctan2(nodes[shell, 2] - centre[1], nodes[shell, 0] - centre[0])
    )
    edges = np.linspace(-180, 180, AZIMUTH_BINS + 1)

    return len(set(np.digitize(azimuth, edges))) >= AZIMUTH_BINS


def beyond_body(nodes, centre):
    """Mask, radius and azimuth of the nodes outside the body, about `centre`."""
    radii = np.hypot(nodes[:, 0] - centre[0], nodes[:, 2] - centre[1])
    azimuth = np.arctan2(nodes[:, 2] - centre[1], nodes[:, 0] - centre[0])

    return radii > BODY_RADIUS + BODY_MARGIN, radii, azimuth


def handle_direction(graphs, name):
    """Unit (x, z) direction of a graph's handle, measured from its own nodes.

    Validated against a rotationally symmetric graph in the same library, which has
    to fit at its own origin with no nodes beyond its body. That check is what
    catches an axis fit that has quietly gone wrong, and without it a phantom lobe
    opposite the real handle looks exactly like a second handle.

    Returns:
        The unit direction and the body axis, or (None, None) with the reason printed.
    """
    centre, votes = fit_body_axis(graphs[name])

    if not is_cylinder(graphs[name], centre):
        print(f" {'':22} {name} has no cylindrical body at {BODY_RADIUS*1000:.0f} mm "
              f"- not a graph this reads handles from")
        return None, None

    if not beyond_body(graphs[name], centre)[0].any():
        print(f" {'':22} {name} has no nodes beyond its body, so it carries no "
              f"handle to track")
        return None, None

    candidates = {
        other: _shell_fraction(graphs[other])
        for other in graphs
        if other != name
    }
    symmetric = [
        other for other, fraction in candidates.items()
        if fraction >= SHELL_FRACTION
    ]

    if not symmetric:
        print(f" {'':22} ! no rotationally symmetric graph to validate the axis fit "
              f"against - cannot trust the handle direction")
        return None, None

    # The best one rather than the first, so graph order cannot decide the answer.
    control = max(symmetric, key=candidates.get)
    # Kept separate from the subject's own centre. Sharing one name here put the
    # control's axis under the subject's nodes and reported the mug's own body wall
    # as a 299-node handle at +177 degrees.
    control_centre, _ = fit_body_axis(graphs[control])

    if max(abs(control_centre[0]), abs(control_centre[1])) > AXIS_TOLERANCE:
        print(f" {'':22} ! the axis fit puts {control}, which is symmetric, at "
              f"({control_centre[0]*1000:+.1f}, {control_centre[1]*1000:+.1f}) mm "
              f"rather than its origin - the fit is unreliable, not reporting "
              f"azimuths")
        return None, None

    stray, _, _ = beyond_body(graphs[control], control_centre)

    if stray.any():
        print(f" {'':22} ! {stray.sum()} nodes of {control} sit beyond its own "
              f"body - it is not the symmetric control this assumes")
        return None, None

    outside, radii, azimuth = beyond_body(graphs[name], centre)

    # Circular mean, so nodes either side of the +-180 wrap do not cancel.
    mean = np.array([
        np.cos(azimuth[outside]).mean(), np.sin(azimuth[outside]).mean()
    ])
    spread = np.hypot(*mean)
    direction = mean / spread

    print(f" {'':22} {name} axis at ({centre[0]*1000:+.1f}, {centre[1]*1000:+.1f}) mm "
          f"({votes} nodes on the body shell); handle {outside.sum()} nodes, "
          f"out to {radii.max()*1000:.1f} mm, direction "
          f"{np.degrees(np.arctan2(direction[1], direction[0])):+.0f} deg, "
          f"concentration {spread:.2f}")

    if spread < 0.5:
        print(f" {'':22} ! those nodes do not point one way - a concentration below "
              f"0.5 means this is not a single handle and the azimuth is meaningless")
        return None, None

    return direction, centre


def _shell_fraction(nodes):
    """Fraction of a graph's nodes lying on the 75 mm body shell about its origin.

    Absence of nodes beyond the body is not enough to identify the symmetric
    control: the 50 mm block is a cube whose corners reach 35.4 mm, inside the
    threshold, so it passes that test and then fits its "axis" 15 mm off its own
    origin, which makes every azimuth below it wrong. A real body of revolution
    puts about half its nodes on the shell; the block puts 8%.

    Returns:
        The fraction on the shell, 0.0 for an empty graph.
    """
    if not len(nodes):
        return 0.0

    radii = np.hypot(nodes[:, 0], nodes[:, 2])

    if (radii > BODY_RADIUS + BODY_MARGIN).any():
        return 0.0

    return float(np.count_nonzero(np.abs(radii - BODY_RADIUS) < 0.003) / len(nodes))


# Measured: rig_glass 47.7%, rig_block 8.3%, rig_mug 10.1%. Anything between those
# separates a body of revolution from a cube; the midpoint is not a tuned number.
SHELL_FRACTION = 0.25

# A cylinder's body shell is populated at every azimuth; a shape that is not one
# leaves gaps. Measured about each graph's own fitted axis: rig_mug and rig_glass
# fill 12 of 12 thirty-degree bins, rig_block fills 5. Without this the 50 mm cube
# gets an axis fitted 15 mm off its origin and reports a confident "handle" at
# +137 degrees, which is nothing at all.
AZIMUTH_BINS = 12


def measure_convention(probe, off_steps, locations, graph):
    """Which of `poses` or its transpose maps sensed motion into the model frame.

    The displacer computes `search = locations + poses.dot(displacement)` on a
    sensor-frame displacement (hypotheses_displacer.py:156), so across two steps that
    hold the same leader, `delta_model = R @ delta_world` under that reading and
    `R.T @ delta_world` under the other. Every episode here is presented at
    [0, 0, 0], where the true pose is the identity and the two are identical, so this
    is measured from the run rather than calibrated against the known rotation.

    Returns:
        "direct", "transpose", or None with the reason printed.
    """
    residuals = {"direct": [], "transpose": []}
    steps = []

    for index in range(len(probe) - 1):
        here, then = probe[index].get(graph), probe[index + 1].get(graph)

        if here is None or then is None:
            continue

        pose, next_pose = np.asarray(here["best_pose"]), np.asarray(then["best_pose"])

        # Only consecutive steps that kept the same leader say anything: across a
        # change of leader the two locations are in different hypotheses' frames.
        if not np.allclose(pose, next_pose, atol=1e-6):
            continue

        delta_world = locations[off_steps[index + 1]] - locations[off_steps[index]]
        delta_model = np.asarray(then["best_location"]) - np.asarray(
            here["best_location"]
        )

        if not np.isfinite(delta_world).all() or np.linalg.norm(delta_world) < 1e-6:
            continue

        residuals["direct"].append(
            np.linalg.norm(pose @ delta_world - delta_model)
        )
        residuals["transpose"].append(
            np.linalg.norm(pose.T @ delta_world - delta_model)
        )
        steps.append(np.linalg.norm(delta_world))

    if len(residuals["direct"]) < 10:
        print(f" {'':22} ! only {len(residuals['direct'])} step pairs hold the same "
              f"leader, too few to measure the rotation convention")
        return None

    direct = float(np.median(residuals["direct"]))
    transpose = float(np.median(residuals["transpose"]))
    print(f" {'':22} convention residuals over {len(residuals['direct'])} pairs: "
          f"direct {direct*1000:.2f} mm, transpose {transpose*1000:.2f} mm")

    better, worse = min(direct, transpose), max(direct, transpose)
    typical = float(np.median(steps))

    # Both tests are needed. The ratio alone cannot separate two residuals that are
    # both near zero - a leader holding the identity rotation makes R and its
    # transpose the same matrix, and `worse < 2 * better` reads 0 < 0 as a decision.
    # The absolute test alone would accept a large constant offset in both.
    # Both tests are needed, and neither is an absolute gap. When the leader sits
    # near the true pose the correct convention fits exactly and the wrong one is
    # still only a fraction of a step out - the mug episode separates 0.00 mm from
    # 0.47 mm on a 10 mm step, which is decisive but small. What must be rejected is
    # the case where *both* fit, which is a leader holding the identity rotation.
    if worse < 2 * better or worse < 0.02 * typical:
        print(f" {'':22} ! the two conventions fit equally well against a typical "
              f"{typical*1000:.1f} mm step, so the azimuths below would be a coin "
              f"toss - not reporting them")
        return None

    return "direct" if direct < transpose else "transpose"


def handle_azimuths(probe, graph, direction, convention):
    """World azimuth of `graph`'s handle for the leading hypothesis, per step."""
    model_dir = np.array([direction[0], 0.0, direction[1]])
    out = []

    for entry in probe:
        record = entry.get(graph)

        if record is None:
            out.append(np.nan)
            continue

        pose = np.asarray(record["best_pose"])
        rotation = pose.T if convention == "direct" else pose
        world = rotation @ model_dir
        out.append(np.degrees(np.arctan2(world[2], world[0])))

    return np.array(out)


def handle_is_visible(azimuth_degrees, handle_radius):
    """Whether the handle at this azimuth escapes the body's silhouette.

    Not a front/back test. A handle pointing along +X is perpendicular to the view
    direction and sticks out sideways to its full radius, so it is plainly visible
    even though it points neither at the camera nor away from it. What hides a
    handle is being *behind* the body and *narrower* than it: the camera looks along
    -z here, so the handle is occluded only when it points away (sin < 0) and its
    outermost point still projects inside the body's radius.

    Returns:
        A boolean array, True where the handle could be seen.
    """
    radians = np.radians(np.asarray(azimuth_degrees, dtype=float))
    behind = np.sin(radians) < 0
    within_silhouette = np.abs(np.cos(radians)) < BODY_RADIUS / handle_radius

    return ~(behind & within_silhouette)


def circular_mean(degrees):
    """Mean of a set of angles, formatted, or a dash if there is nothing to average.

    Averaged as unit vectors rather than as numbers, so a leader sitting near the
    +-180 wrap does not average to 0 and read as pointing the opposite way.

    Returns:
        A five-character field, for printing in a row of deciles.
    """
    values = np.asarray(degrees, dtype=float)
    values = values[np.isfinite(values)]

    if not len(values):
        return "  -  "

    radians = np.radians(values)
    mean = np.degrees(np.arctan2(np.sin(radians).mean(), np.cos(radians).mean()))

    return f"{mean:+5.0f}"


def pose_report(paths, names):
    """Where the leading hypothesis puts the handle, and whether that moves.

    The rotation convention is a property of the code, not of a run, so it is
    measured once across every run given and then shared. That matters because a
    run whose leader holds a symmetric pose cannot separate the two - on the
    simulated glass episode the mug's leader fits both at 0.00 mm - while the mug
    episode separates them cleanly. Pass both and the answerable one settles it.
    """
    loaded, failures = [], 0

    for path, name in zip(paths, names):
        print()

        logged, off_steps, on_object = probe_against_steps(path, name)

        if logged is None:
            failures += 1
            continue

        if "best_pose" not in next(iter(logged[0].values())):
            print(f" {name:22} the probe recorded no best_pose - this run predates "
                  f"the field, re-run it")
            failures += 1
            continue

        graphs = read_graphs(path)
        locations = np.array([
            np.asarray(step[2], dtype=float) if step[2] is not None
            else np.full(3, np.nan)
            for step in read_run(path)
        ])
        loaded.append((name, logged, off_steps, locations, graphs))

    conventions = {}

    for name, logged, off_steps, locations, graphs in loaded:
        for graph in sorted(graphs):
            if graph not in logged[0]:
                continue

            print(f" {name:22} measuring the convention on {graph}")
            settled = measure_convention(logged, off_steps, locations, graph)

            if settled is not None:
                conventions.setdefault(settled, []).append(f"{name}/{graph}")

    if len(conventions) != 1:
        print()
        summary = "; ".join(
            f"{reading} from {', '.join(sources)}"
            for reading, sources in conventions.items()
        )
        print(f" ! the rotation convention came out "
              f"{'inconsistent' if conventions else 'ambiguous'}"
              f"{': ' + summary if summary else ''}. Pass a run whose leader holds "
              f"a non-symmetric pose - the simulated mug episode does - and do not "
              f"read azimuths until one convention wins outright.")
        return 1

    convention = next(iter(conventions))
    print()
    print(f" convention: {convention}, from "
          f"{', '.join(next(iter(conventions.values())))}")

    for name, logged, off_steps, locations, graphs in loaded:
        print()

        for graph in sorted(graphs):
            if graph not in logged[0]:
                continue

            direction, _ = handle_direction(graphs, graph)

            if direction is None:
                continue

            azimuth = handle_azimuths(logged, graph, direction, convention)
            valid = np.isfinite(azimuth)

            if not valid.any():
                continue

            centre, _ = fit_body_axis(graphs[graph])
            _, radii, _ = beyond_body(graphs[graph], centre)
            visible = handle_is_visible(azimuth, float(radii.max()))
            leaders = sum(
                1 for i in range(len(logged) - 1)
                if graph in logged[i] and graph in logged[i + 1]
                and not np.allclose(
                    np.asarray(logged[i][graph]["best_pose"]),
                    np.asarray(logged[i + 1][graph]["best_pose"]),
                    atol=1e-6,
                )
            )

            print(f" {'':22} {graph}: leading hypothesis changed {leaders} times; "
                  f"handle visible on {100*visible[valid].mean():.1f}% of "
                  f"{int(valid.sum())} steps")
            print(f" {'':22} handle azimuth by decile: " + " ".join(
                circular_mean(chunk) for chunk in np.array_split(azimuth, 10)
            ))

            first, last = azimuth[valid][0], azimuth[valid][-1]
            moved = abs((last - first + 180) % 360 - 180)
            started_visible = bool(visible[valid][0])
            ended_visible = bool(visible[valid][-1])

            if started_visible and not ended_visible:
                verdict = ("started visible and ended hidden - the mechanism drove "
                           "the leader out of the falsifiable region")
            elif started_visible:
                verdict = ("started and ended visible - the leader was contradictable "
                           "throughout and was not displaced")
            elif not started_visible and moved < 15:
                verdict = ("started hidden and barely moved - this episode never "
                           "tested the mechanism, re-run from a pose that shows "
                           "the handle")
            else:
                verdict = "started hidden and moved"

            print(f" {'':22} {first:+.0f} deg -> {last:+.0f} deg ({moved:.0f} deg "
                  f"of travel): {verdict}")

    print()

    if failures:
        print(" ! the poses could not be read on every run. This mode needs the "
              "probe's best_pose and best_location fields, a handle direction the "
              "graphs support, and enough same-leader step pairs to fix the "
              "rotation convention.")
        return 1

    print(" read the leader's travel, not the contradicted fractions: a handle "
          "behind the body is unfalsifiable from one viewpoint however well the "
          "mechanism works, so only the visible ones test it.")

    return 0


def main():
    args = sys.argv[1:]

    identity = bool(args) and args[0] == IDENTITY_FLAG
    depths = bool(args) and args[0] == DEPTHS_FLAG
    in_model = bool(args) and args[0] == IN_MODEL_FLAG
    gap = bool(args) and args[0] == GAP_FLAG
    reentry = bool(args) and args[0] == REENTRY_FLAG
    pose = bool(args) and args[0] == POSE_FLAG

    if identity or depths or in_model or gap or reentry or pose:
        args = args[1:]

    # Depth, in-model, gap and re-entry mode read each run on its own terms, so one is
    # a complete check; the excursion and identity reports are comparisons and need
    # two. Re-entry's second run is a control rather than the other object, which is
    # the one place a second argument means something different - see its section.
    if len(args) not in (
        (1, 2) if depths or in_model or gap or reentry or pose else (2,)
    ):
        print(__doc__.strip())
        return 1

    paths = [Path(argument).expanduser() for argument in args]

    for path in paths:
        # An OOM kill during save_state_dir leaves a 0-byte model.pt behind.
        if not path.exists() or path.stat().st_size == 0:
            print(f" ! {path} is missing or empty")
            return 1

    names = [path.parent.parent.name for path in paths]
    labels = [read_episode_label(path) for path in paths]

    for name, (target, rotation, _) in zip(names, labels):
        print(f" {name:22} saved episode: {target} at {rotation}")

    if depths:
        return depths_report(paths, names)

    if in_model:
        return in_model_report(paths, names)

    if gap:
        return gap_report(paths, names)

    if reentry:
        return reentry_report(paths, names)

    if pose:
        return pose_report(paths, names)

    if identity:
        return identity_report(paths, names, labels)

    rotations = [rotation for _, rotation, _ in labels]

    if all(rotations) and rotations[0] != rotations[1]:
        print(f" ! the saved episodes are at different rotations, so step i is not "
              f"the same view in both. Set n_eval_epochs to 1 and give each run the "
              f"same single rotation.")
        return 1

    targets = [target for target, _, _ in labels]

    if all(targets) and targets[0] == targets[1]:
        print(f" ! both runs saved a {targets[0]} episode. Objects cycle within an "
              f"epoch, so a run over both saves only the last one - give each run a "
              f"single object.")
        return 1

    if not check_comparable([read_run_config(path) for path in paths], names):
        print()

    runs = [read_run(path) for path in paths]

    print()

    left_object = describe(names[0], runs[0])
    left_object |= describe(names[1], runs[1])

    overlap = min(len(runs[0]), len(runs[1]))

    if len(runs[0]) != len(runs[1]):
        print(f" {'':22} step counts differ, comparing the first {overlap}")

    disagreement = [
        index for index in range(overlap)
        if runs[0][index][0] and not runs[1][index][0]
    ]

    if disagreement:
        print(f" {'':22} {len(disagreement)} steps where {names[0]} is on object "
              f"and {names[1]} is not, first at {disagreement[0]}, last at "
              f"{disagreement[-1]}")
    else:
        print(f" {'':22} no disagreement")

    position = labels[0][2]

    if disagreement and position is not None and len(position) == 3:
        where_they_differ(runs, names, disagreement, position)

    print()

    if not left_object:
        print(" ! every excursion is one step or shorter, so the policy did not "
              "take. Check that naive_scan_5 is the motor system actually loaded.")
        return 1

    if not disagreement:
        print(" ! the spiral leaves the object but never crosses the region where "
              "the two disagree, so flipping the mask would change nothing here. "
              "Raise fixed_amount or start off-centre; do not proceed to stage 2.")
        return 1

    print(" the spiral leaves the object and crosses the region where the two "
          "disagree, so stage 1 is passed and stage 2 has something to act on.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
