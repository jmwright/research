from __future__ import annotations

from tbp.monty.cmp import Goal, Message
from tbp.monty.context import RuntimeContext
from tbp.monty.frameworks.models.abstract_monty_classes import Observations
from tbp.monty.frameworks.models.motor_policies import MotorPolicyResult, NaiveScanPolicy
from tbp.monty.frameworks.models.motor_system_state import MotorSystemState


class RescanningNaiveScanPolicy(NaiveScanPolicy):
    """NaiveScanPolicy that can restart its spiral after a goal-driven jump.

    Used as the `default` policy of a `DistantPolicySelector`, which runs
    `JumpToGoal` on steps where the LM's GSG proposes a goal and this policy on
    every other step. The spiral is therefore never handed a goal and needs no
    goal handling of its own; `check_cycle_action` advances only on the steps
    this policy is actually called, so the jump and undo steps leave the walk
    where they found it.

    What the composition does not settle is the *anchor*. The spiral's actions are
    relative rotations, so after a jump that is kept, resuming at arm length k
    walks the sensor up to 5k degrees away from the viewpoint the goal just chose
    - the spiral spends the viewpoint before looking at it. With
    `rescan_after_jump` the spiral restarts instead, centred on the new pose.

    Both are defensible and they are not the same experiment, so it is a flag:
    False reproduces the plain composition exactly, True asks whether a chosen
    viewpoint is worth a fresh local scan. The cost of True is that the walk never
    reaches 90 degrees of arm within the step budget, which is the thing stage 1
    was set up to guarantee.
    """

    def __init__(self, rescan_after_jump: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rescan_after_jump = rescan_after_jump
        self._agent_pose_at_last_step = None

    def reset(self) -> None:
        super().reset()
        self._agent_pose_at_last_step = None

    def __call__(
        self,
        ctx: RuntimeContext,
        observations: Observations,
        state: MotorSystemState,
        percept: Message,
        goal: Goal | None,
    ) -> MotorPolicyResult:
        # The selector does not tell the default policy that a jump happened, and
        # a jump the selector undid must not count - the spiral is back where it
        # was and restarting it would double-cover. The agent's own pose is the
        # only witness to both at once: it differs from the last pose this policy
        # saw exactly when a jump was kept.
        if self.rescan_after_jump and self._jumped_since_last_step(state):
            self._init_NaiveScanPolicy()

        self._agent_pose_at_last_step = self._agent_pose(state)

        return super().__call__(ctx, observations, state, percept, goal)

    def _agent_pose(self, state: MotorSystemState):
        agent = state[self.agent_id]
        return (tuple(agent.position), agent.rotation)

    def _jumped_since_last_step(self, state: MotorSystemState) -> bool:
        if self._agent_pose_at_last_step is None:
            return False

        previous_position, previous_rotation = self._agent_pose_at_last_step
        position, rotation = self._agent_pose(state)

        # A spiral step is a pure rotation of the distant agent, which pivots like
        # a ball-and-socket joint and never translates. Only `SetAgentPose` moves
        # it, so position alone separates a jump from a scan step.
        return position != previous_position
