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

# naive_scan_5_goal.yaml resolves stereo_policies by import. Without this the
# failure is an InterpolationResolutionError at config time that never names the
# missing module.
export PYTHONPATH=$HERE:$PYTHONPATH

LM=++experiment.config.monty_config.learning_modules.learning_module_0
mkdir -p "$OUT"

for OBJ in mug block glass bar i; do
  case $OBJ in
    bar|i) LIB=rig_letters;  MODEL=$HERE/models/rig_letters_surf_agent/pretrained/ ;;
    *)     LIB=rig_objects;  MODEL=$HERE/models/rig_objects_3obj/pretrained/ ;;
  esac
  echo "=== rig_$OBJ (strikes=$STRIKES, budget=$BUDGET) ==="
  ( cd "$MONTY" && python run.py --config-dir "$HERE/conf" \
      experiment=rig_eval_sim_goal \
      experiment.config.environment.env_init_args.data_path="$HERE/libraries/$LIB" \
      ++experiment.config.eval_env_interface_args.object_names=[rig_$OBJ] \
      experiment.config.model_name_or_path="$MODEL" \
      experiment.config.logging.run_name=rig_$OBJ \
      experiment.config.logging.output_dir="$OUT" \
      ++experiment.config.match_criterion.count=1 \
      ++experiment.config.n_eval_epochs=3 \
      ++experiment.config.eval_env_interface_args.object_init_sampler.rotations="[[0,0,0],[0,90,0],[0,180,0]]" \
      ++experiment.config.monty_config.motor_system_config.policy_selector.default.rescan_after_jump=true \
      $LM.goals_from_off_object=true \
      $LM.max_match_distance=0.01 \
      $LM.hypotheses_updater_args.off_object_contradiction=4.0 \
      $LM.hypotheses_updater_args.off_object_ray_carve=true \
      $LM.hypotheses_updater_args.off_object_refutation_strikes=$STRIKES \
      '~experiment.config.recognition_policy.fixed_amount' \
      ++experiment.config.recognition_policy._target_=tbp.monty.experiment.recognition_policy.MaxTotalSteps \
      ++experiment.config.recognition_policy.max_total_steps=$BUDGET \
      ++experiment.config.max_eval_steps=$((BUDGET + 1000)) ) > "$OUT/rig_$OBJ.stdout" 2>&1 \
    || echo "FAILED rig_$OBJ - see $OUT/rig_$OBJ.stdout"
done

"$HERE/score.py" "$OUT"
