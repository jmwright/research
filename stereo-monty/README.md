# stereo-monty

Getting [Monty](https://github.com/thousandbrainsproject/tbp.monty) to terminate on an
object whose model is a strict subset of another object's.

The case is a **mug** and a **glass**, where the glass is the mug's body with the handle
removed and nothing else changed. Every point on the glass is also on the mug, so no
amount of looking at a glass produces evidence the mug hypothesis cannot explain. Monty
accumulates positive evidence, and the absence of a handle is never itself an
observation - so `rig_mug` is never eliminated, the terminal condition never fires, and
the episode runs to its step ceiling with two objects still alive. The correct object
leads the whole way. What is missing is a way to *stop*.

This is Thousand Brains' own worked example, written up as two Future Work items,
[Use Off Object Observations](https://docs.thousandbrains.org/docs/use-off-object-observations)
and [Use Out of Model Movements](https://docs.thousandbrains.org/docs/use-out-of-model-movements),
both approved and both unimplemented.

**In simulation this now terminates correctly on all five test objects at three
rotations each: 15 of 15, no time-outs and no wrong answers**, against 9 of 15 for the
same configuration without the change.

## What the change is

Every disconfirmation mechanism tried before this one fed the null observation back into
*continuous evidence*: a hypothesis that predicted surface where the sensor found none
lost points. That cannot finish. An object scores the **max** over its hypotheses, so
knocking the leader down just promotes the runner-up - and the runners-up are dense, with
the best and second-best differing by 0.02 in one measured case.

**Refutation changes the operator instead of the magnitude.** Count how many null
observations have passed through the volume a hypothesis says is solid, and at a
threshold remove that hypothesis *permanently*. Accumulated support becomes irrelevant:
300 body observations of a 781-node graph cannot save a pose whose predicted surface has
been looked through. The twin is killed too, and the one after that, and the pose space
is finite - so the stalemate turns into an exhaustion.

Two details that matter:

- **A ray, not a point.** A null pixel does not assert "no surface at this depth", it
  asserts "no surface anywhere along this ray". Testing the ray is also what makes
  self-occlusion a non-problem: a hypothesis hiding its handle behind its own body
  predicts surface that cannot be seen, and a point probe there would need a visibility
  test to avoid killing it. A null ray needs none, because an occluder would have
  produced a hit rather than a null.
- **A strike count, not a single strike.** The ray test strikes the *correct* object on
  about 2 of every 266 off-object rays, so a permanent kill on one strike would
  eventually destroy a true hypothesis. The count is what buys safety, not the aim.

## What else it needed, which is half the reason it was missed

**A step budget past the search policy's own length.** `NaiveScan` at `fixed_amount: 5`
ends at `k*(k-1)+1 = 307` steps. The glass needs 490 to 1160. Every earlier experiment
was scored at 307, where refutation also fails, so the pair was never tried together:

| refutation | budget | correct, of 15 |
|---|---|---|
| off | 307 | 9 |
| on | 307 | 10 |
| **off** | **3000** | **9** |
| **on** | **3000** | **15** |

The budget on its own buys nothing. Reading a policy's own ceiling as a property of the
problem is the mistake this took the longest to find.

## Running it

You need the fork of tbp.monty that carries the mechanism:

```
git clone -b use-off-object-observations https://github.com/jmwright/tbp.monty
```

Follow tbp.monty's own [installation
instructions](https://thousandbrainsproject.readme.io/docs/getting-started) for the
environment - this needs nothing on top of it. Then, from anywhere:

```
./run_gate.sh <tbp.monty checkout> <output dir>
```

which runs five objects at three rotations each and prints:

```
mug    correct(26) | correct(86) | correct(26)
block  correct(37) | correct(28) | correct(37)
glass  correct(643) | correct(1092) | correct(702)
bar    correct(2396) | correct(407) | correct(83)
i      correct(32) | correct(77) | correct(157)

correct: 15

15 of 15
```

The numbers in brackets are the step each episode terminated on, and they are
reproducible to the step. For the before picture, pass `0` as the strike threshold:

```
./run_gate.sh <tbp.monty checkout> <output dir> 0     # 9 of 15
```

That control is worth running. It is the same code, the same budget and the same
policy, with only the strike threshold at zero.

### The knob

One parameter, on the learning module's hypotheses updater:

```
hypotheses_updater_args.off_object_refutation_strikes: 3
```

`0` disables it and reproduces stock behaviour exactly. It requires
`off_object_ray_carve: true`, which is what computes the strike, and
`process_off_object: true`, which is what lets the learning module see a null at all.
It does **not** replace `off_object_contradiction`; the penalty still runs underneath
and is load-bearing for one of the five objects.

Each matching step logs a greppable line, one entry per object in the library:

```
refutation: rig_mug 391/398 650.160 | rig_glass 183/363 683.496
refutation: rig_mug 398/398 dead    | rig_glass 248/359 737.364
```

reading `<object> <refuted>/<total> <best surviving evidence>`. Watching that count
climb is the clearest view of the mechanism there is.

## What is here

| | |
|---|---|
| `run_gate.sh` | the five-object, three-rotation acceptance gate |
| `score.py` | reads the gate's `eval_stats.csv` files into the table above |
| `conf/` | Hydra configs - `rig_eval_sim_goal` is the settled one |
| `stereo_policies.py` | `RescanningNaiveScanPolicy`, resolved by name from the motor config |
| `libraries/` | the two object libraries, built and committed |
| `cad/` | the CadQuery sources the five test objects were printed from |
| `models/` | pretrained models for both libraries |
| `make_object_library.py` | STL directory to a Habitat object library |
| `compare_excursions.py` | run-log analysis used while developing this |

### The objects

Five printed parts, in two libraries, chosen so that each isolates one thing:

| object | library | why it is here |
|---|---|---|
| `rig_mug` | `rig_objects` | the object with the extra feature |
| `rig_glass` | `rig_objects` | the mug's body, handle removed - **the case this is about** |
| `rig_block` | `rig_objects` | a 50 mm cube, so the library is not just two cylinders |
| `rig_bar` | `rig_letters` | a plain vertical bar |
| `rig_i` | `rig_letters` | the same bar with a dot above it, a 26 mm gap between |

The bar/i pair is the same problem with the discriminating feature as a *void* rather
than a *protrusion*, and with the pose space collapsed 13-fold by making that void
rotationally symmetric. It is the harder of the two and remains the fragile one - see
the caveats.

Because these were printed, the library is **ground-truth CAD geometry rather than a
reconstruction**. Rebuilding it is optional; the `.glb` files under `libraries/` are
committed and every number here is tied to those exact meshes.

### Rebuilding, if you want to

Export STLs from `cad/*.py` (needs [CadQuery](https://github.com/CadQuery/cadquery)),
then:

```
./make_object_library.py <stl dir> libraries/rig_objects
```

A faithful rebuild pretrains to **`rig_mug` 781, `rig_block` 1201, `rig_glass` 556**
graph points. Either number moving means the CAD and the committed meshes have parted.

To regenerate the pretrained models rather than use the ones in `models/`:

```
cd <tbp.monty checkout>
export PYTHONPATH=<this directory>

python run.py --config-dir <this directory>/conf \
  experiment=rig_pretrain \
  experiment.config.environment.env_init_args.data_path=<this directory>/libraries/rig_objects \
  +experiment.config.train_env_interface_args.object_names=[rig_mug,rig_block,rig_glass] \
  experiment.config.logging.run_name=rig_objects_3obj \
  experiment.config.logging.output_dir=<this directory>/models
```

and the same again with `libraries/rig_letters`, `[rig_bar,rig_i]` and
`run_name=rig_letters_surf_agent`. The two run names are what `run_gate.sh` looks for
under `models/`, so keep them or edit the script.

## Caveats, in order of how much they should bother you

**This is simulation, and the agent moves.** The policy teleports a distant agent around
the object. Read back from one glass run's own goal trace: 17 achieved goals spanning
**-177 to +134 degrees of azimuth**, 8 of them on the far hemisphere, one 151 mm
overhead. **Nothing here shows that a single viewpoint is enough.** If you want to apply
this to a fixed camera, that is the first thing to establish, not the last.

**It has not been tried on real sensor data.** In simulation a null observation means
"there is nothing there". On a real depth sensor it means that *or* "I could not measure
here" - a stereo matcher failing on a smooth surface produces exactly the same void as
empty space. Refutation is a permanent kill, so a null that really meant "I could not
measure" would destroy the true hypothesis and nothing would bring it back. **This is the
assumption the whole mechanism rests on, and it is the one simulation cannot test.**

**Its failure mode is a confident wrong answer, not an abstention.** Across the
thresholds tried (1, 2, 3, 5) the `rig_bar` case is not monotone - 3 of 3, then 2 of 3
with one wrong answer, then 3 of 3, then 1 of 3 - because refutation moves the leading
hypothesis, which moves the goals, which moves the trajectory. One bar episode is a coin
toss. The mug/glass case is not like this: correct in 12 of 12 episodes across all four
thresholds, and 3 of 3 more with the penalty disabled entirely.

**The budget is not free.** 490 to 2396 steps for a five-object library, against 26 to 86
for the easy cases.

**Three or five alternatives is a thin recognition task.** How this behaves against a
library of a hundred objects is untested. A single ray refutes many hypotheses across
many objects at once, so it should not scale badly, but "should not" is doing real work
in that sentence.

**It departs from the approved design.** Thousand Brains' RFCs specify a *penalty* for an
off-object observation, not an elimination. This is the same diagnosis with a harder
operator, and the trade is the failure mode above: a penalty degrades gracefully, this
does not.

## The long version

This directory is the result. The reasoning - eight stages, four of which concluded the
problem was structurally unsolvable and were wrong - is in the original repository's
[`depth/monty/README.md`](https://github.com/jmwright/stereoscopic-camera-trap/blob/main/depth/monty/README.md),
along with the per-stage predictions written before each run and scored afterwards. The
superseded conclusions are marked rather than deleted, which is the point of keeping it.

The short version of what went wrong for three weeks: the subsumption argument -
*every point on a glass is also on a mug, so positive evidence cannot separate them* - is
true, and was silently extended to negative evidence, where it does not hold. The mug's
model has 225 nodes the glass's does not. A null ray through that volume refutes a mug
pose and says nothing whatever about a glass pose.

## Provenance

The rig this came from is a two-camera ESP32-CAM stereo trap; the five objects are
printed parts photographed on it. None of that is needed here - this directory is
self-contained and runs entirely in Habitat.

The mechanism lives on the `use-off-object-observations` branch of
[jmwright/tbp.monty](https://github.com/jmwright/tbp.monty), in
`hypotheses_displacer.py`, `burst_sampling.py` and
`evidence_matching/learning_module.py`.
