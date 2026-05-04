# Intentionally empty.
#
# Convenience top-level re-exports were removed because they triggered a
# circular import chain at package initialization
# (algorithm.__init__ → base.alg_spec → parameters.ffc_params → io →
# parallel_mc_cost_heatmap → algorithm). All callers already use
# submodule paths (e.g. ``from ffc_ddw_sum_et.algorithm.dispatcher.bn2d
# import BN2DDispatcher``), so no functional regression.
