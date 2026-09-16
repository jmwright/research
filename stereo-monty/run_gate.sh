#!/bin/bash
# The acceptance gate: five objects x three rotations, scored as one run each.
#
#   ./run_gate.sh <tbp.monty checkout> <output dir> [strikes] [budget]
#
# Defaults are the settled configuration: 3 strikes, 3000 steps. Pass 0 strikes to
# get the pre-stage-8 baseline for comparison - it should score 9 of 15.
set -e
MONTY=${1:?usage: run_gate.sh <tbp.monty checkout> <output dir> [strikes] [budget]}
OUT=${2:?usage: run_gate.sh <tbp.monty checkout> <output dir> [strikes] [budget]}
STRIKES=${3:-3}
BUDGET=${4:-3000}
HERE=$(cd "$(dirname "$0")" && pwd)

# Checked up front, because the alternative is five runs that each fail into their
# own log file and one "command not found" at the end. Override PYTHON if your
# interpreter is named something else; do not let it fall back to a system python3,
# which would fail later and less clearly.
PYTHON=${PYTHON:-python}
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "error: '$PYTHON' is not on PATH." >&2
  echo "Activate the tbp.monty environment first, e.g. 'conda activate tbp.monty'," >&2
  echo "or run with PYTHON=/path/to/python $0 ..." >&2
  exit 1
fi
if ! "$PYTHON" -c "import tbp.monty" >/dev/null 2>&1; then
  echo "error: '$(command -v "$PYTHON")' cannot import tbp.monty." >&2
  echo "Activate the environment you installed tbp.monty into, or set PYTHON." >&2
  exit 1
fi
if [ ! -d "$MONTY" ] || [ ! -f "$MONTY/run.py" ]; then
  echo "error: '$MONTY' does not look like a tbp.monty checkout (no run.py)." >&2
  exit 1
fi

# naive_scan_5_goal.yaml resolves stereo_policies by import. Without this the
# failure is an InterpolationResolutionError at config time that never names the
# missing module.
export PYTHONPATH=$HERE:$PYTHONPATH

# Everything that defines the method lives in conf/experiment/rig_eval_sim_goal.yaml.
# The only things overridden here are what this script takes as arguments and what
# the gate itself defines - the object, its library and model, the three rotations,
# and a terminal condition that ends an episode when the learning module decides.
LM=++experiment.config.monty_config.learning_modules.learning_module_0
mkdir -p "$OUT"

for OBJ in mug block glass bar i; do
  case $OBJ in
    bar|i) LIB=rig_letters;  MODEL=$HERE/models/rig_letters_surf_agent/pretrained/ ;;
    *)     LIB=rig_objects;  MODEL=$HERE/models/rig_objects_3obj/pretrained/ ;;
  esac
  echo "=== rig_$OBJ (strikes=$STRIKES, budget=$BUDGET) ==="
  ( cd "$MONTY" && "$PYTHON" run.py --config-dir "$HERE/conf" \
      experiment=rig_eval_sim_goal \
      experiment.config.environment.env_init_args.data_path="$HERE/libraries/$LIB" \
      ++experiment.config.eval_env_interface_args.object_names=[rig_$OBJ] \
      experiment.config.model_name_or_path="$MODEL" \
      experiment.config.logging.run_name=rig_$OBJ \
      experiment.config.logging.output_dir="$OUT" \
      ++experiment.config.match_criterion.count=1 \
      ++experiment.config.n_eval_epochs=3 \
      ++experiment.config.eval_env_interface_args.object_init_sampler.rotations="[[0,0,0],[0,90,0],[0,180,0]]" \
      $LM.hypotheses_updater_args.off_object_refutation_strikes=$STRIKES \
      '~experiment.config.recognition_policy.fixed_amount' \
      ++experiment.config.recognition_policy._target_=tbp.monty.experiment.recognition_policy.MaxTotalSteps \
      ++experiment.config.recognition_policy.max_total_steps=$BUDGET \
      ++experiment.config.max_eval_steps=$((BUDGET + 1000)) ) > "$OUT/rig_$OBJ.stdout" 2>&1 \
    || echo "FAILED rig_$OBJ - see $OUT/rig_$OBJ.stdout"
done

"$PYTHON" "$HERE/score.py" "$OUT"
