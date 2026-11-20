"""Superseded -- deliberately NOT registered as a tool (ADR-036). The platform already has
`aura_core/domain/planning.py` (`Plan._assert_acyclic`, `Plan.ready_steps`), which the planning
engine already uses to reject cyclic plans and order steps. This class's recursive DFS also raises
`RecursionError` on a dependency chain a few thousand long.

Kept for reference only: nothing under ``aura_core`` may import it
(``tests/architecture/test_superseded_open_tools_unused.py``).
"""

from collections import defaultdict
from typing import Any, Dict, Iterator, List, Optional, Set

# Note: 'deque' was imported previously but is no longer needed after the
# recursive DFS was replaced with an explicit stack-based iterative DFS.


class WorkflowDependencyValidator:
    """DAG validator for workflow task dependencies, cycle detection, topological ordering, and critical path analysis.

    Bugs fixed (v2):
    - detect_cycles now uses an iterative DFS instead of a recursive one, so a
      3,000-task linear chain no longer triggers Python's RecursionError.
    """

    @staticmethod
    def detect_cycles(tasks_graph: Dict[str, List[str]]) -> Dict[str, Any]:
        """Validate a task dependency graph where tasks_graph[task] = [prerequisite_tasks].

        Uses an iterative DFS with a three-colour (WHITE=0, GREY=1, BLACK=2)
        node-state scheme to detect back-edges (cycles) without recursion.
        Safe for graphs with tens of thousands of nodes.
        """
        # Collect every node mentioned in keys or dependency lists
        all_nodes: Set[str] = set(tasks_graph.keys())
        for deps in tasks_graph.values():
            all_nodes.update(deps)

        # 0 = unvisited, 1 = on current path (grey), 2 = finished (black)
        state: Dict[str, int] = {n: 0 for n in all_nodes}
        cycles: List[List[str]] = []

        for start_node in sorted(all_nodes):
            if state[start_node] != 0:
                continue

            # Each stack frame is (node, iterator-over-children)
            # 'path' mirrors the current DFS path so we can record cycles.
            path: List[str] = [start_node]
            path_set: Set[str] = {start_node}
            state[start_node] = 1

            child_iters: List[Iterator[str]] = [
                iter(tasks_graph.get(start_node, []))
            ]

            while child_iters:
                try:
                    child = next(child_iters[-1])
                except StopIteration:
                    # Back out of this frame
                    finished_node = path[-1]
                    state[finished_node] = 2
                    path.pop()
                    path_set.discard(finished_node)
                    child_iters.pop()
                    continue

                child_state = state.get(child, 0)
                if child_state == 1:
                    # Back-edge → cycle
                    idx = path.index(child)
                    cycles.append(path[idx:] + [child])
                elif child_state == 0:
                    state[child] = 1
                    path.append(child)
                    path_set.add(child)
                    child_iters.append(iter(tasks_graph.get(child, [])))
                # child_state == 2 → already fully explored, skip

        return {
            "workflow_valid": len(cycles) == 0,
            "circular_dependencies_detected": len(cycles) > 0,
            "cycles": cycles,
        }

    @classmethod
    def topological_sort(cls, tasks_graph: Dict[str, List[str]]) -> Dict[str, Any]:
        """Return a valid linear execution order if the workflow graph is acyclic."""
        cycle_check = cls.detect_cycles(tasks_graph)
        if cycle_check["circular_dependencies_detected"]:
            return {
                "valid": False,
                "order": [],
                "cycles": cycle_check["cycles"],
            }

        all_nodes: Set[str] = set(tasks_graph.keys())
        for deps in tasks_graph.values():
            all_nodes.update(deps)

        remaining_deps: Dict[str, set] = {
            node: set(tasks_graph.get(node, [])) for node in all_nodes
        }
        order: List[str] = []

        while remaining_deps:
            ready = sorted([n for n, deps in remaining_deps.items() if len(deps) == 0])
            if not ready:
                return {"valid": False, "order": [], "error": "Unable to resolve remaining dependencies."}
            for n in ready:
                order.append(n)
                del remaining_deps[n]
            for deps in remaining_deps.values():
                deps.difference_update(ready)

        return {"valid": True, "order": order, "cycles": []}

    @classmethod
    def get_parallel_execution_tiers(cls, tasks_graph: Dict[str, List[str]]) -> Dict[str, Any]:
        """Partition DAG into sequential stages/tiers where all tasks in each tier can execute in parallel."""
        topo = cls.topological_sort(tasks_graph)
        if not topo["valid"]:
            return {"valid": False, "tiers": [], "error": "Graph has cycles; cannot build parallel tiers."}

        node_tier: Dict[str, int] = {}
        for node in topo["order"]:
            prereqs = tasks_graph.get(node, [])
            if not prereqs:
                node_tier[node] = 0
            else:
                node_tier[node] = max(node_tier[p] for p in prereqs) + 1

        tier_buckets: Dict[int, List[str]] = defaultdict(list)
        for node, tier_idx in node_tier.items():
            tier_buckets[tier_idx].append(node)

        tiers = [tier_buckets[i] for i in range(len(tier_buckets))]
        return {
            "valid": True,
            "total_tiers": len(tiers),
            "tiers": tiers,
            "max_concurrency": max((len(t) for t in tiers), default=0),
        }

    @classmethod
    def calculate_critical_path(
        cls,
        tasks_graph: Dict[str, List[str]],
        task_durations: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Identify critical path duration and bottleneck tasks in the DAG."""
        topo = cls.topological_sort(tasks_graph)
        if not topo["valid"]:
            return {"valid": False, "critical_path": [], "critical_duration": 0.0}

        all_nodes = topo["order"]
        durations = task_durations or {n: 1.0 for n in all_nodes}

        earliest_start: Dict[str, float] = {n: 0.0 for n in all_nodes}
        pred_on_longest: Dict[str, Optional[str]] = {n: None for n in all_nodes}

        for n in all_nodes:
            prereqs = tasks_graph.get(n, [])
            if prereqs:
                best_pred = max(prereqs, key=lambda p: earliest_start[p] + durations.get(p, 1.0))
                earliest_start[n] = earliest_start[best_pred] + durations.get(best_pred, 1.0)
                pred_on_longest[n] = best_pred

        end_node = max(all_nodes, key=lambda n: earliest_start[n] + durations.get(n, 1.0))
        max_duration = earliest_start[end_node] + durations.get(end_node, 1.0)

        path: List[str] = []
        curr: Optional[str] = end_node
        while curr is not None:
            path.append(curr)
            curr = pred_on_longest[curr]
        path.reverse()

        return {
            "valid": True,
            "critical_duration": round(max_duration, 3),
            "critical_path": path,
            "terminal_task": end_node,
        }

    @classmethod
    def validate_workflow_spec(cls, workflow_spec: Dict[str, Any]) -> Dict[str, Any]:
        """Validate comprehensive workflow structure with tasks, prerequisites, and deadlines."""
        tasks = workflow_spec.get("tasks", [])
        if not tasks:
            return {"valid": False, "errors": ["Workflow specification contains no tasks."]}

        errors: List[str] = []
        task_ids: Set[str] = set()
        graph: Dict[str, List[str]] = {}

        for t in tasks:
            tid = t.get("id") or t.get("name")
            if not tid:
                errors.append("Task found without an 'id' or 'name'")
                continue
            if tid in task_ids:
                errors.append(f"Duplicate task id: '{tid}'")
            task_ids.add(tid)
            graph[tid] = t.get("dependencies", []) or t.get("prerequisites", [])

        for tid, deps in graph.items():
            for d in deps:
                if d not in task_ids:
                    errors.append(f"Task '{tid}' references undefined prerequisite '{d}'")

        if errors:
            return {"valid": False, "errors": errors}

        cycle_res = cls.detect_cycles(graph)
        if cycle_res["circular_dependencies_detected"]:
            return {
                "valid": False,
                "errors": [f"Cyclic dependency detected: {' -> '.join(c)}" for c in cycle_res["cycles"]],
            }

        tiers_res = cls.get_parallel_execution_tiers(graph)
        return {
            "valid": True,
            "errors": [],
            "total_tasks": len(task_ids),
            "parallel_tiers": tiers_res.get("tiers", []),
            "concurrency_factor": tiers_res.get("max_concurrency", 1),
        }
