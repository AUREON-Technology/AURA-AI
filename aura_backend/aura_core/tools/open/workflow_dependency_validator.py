"""Superseded -- deliberately NOT registered as a tool (ADR-036). The platform already has
`aura_core/domain/planning.py` (`Plan._assert_acyclic`, `Plan.ready_steps`), which the planning
engine already uses to reject cyclic plans and order steps. This class's recursive DFS also raises
`RecursionError` on a dependency chain a few thousand long.

Kept for reference only: nothing under ``aura_core`` may import it
(``tests/architecture/test_superseded_open_tools_unused.py``).
"""

import math
from collections import defaultdict
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

# Note: the DFS below is an explicit-stack iterative one (a recursive version raised
# RecursionError on a few-thousand-long chain), and the ordering is Kahn's algorithm, so every
# method is linear in nodes + edges.


def _normalize_graph(tasks_graph: Any) -> Tuple[Dict[str, List[str]], Optional[str]]:
    """``(graph, error)``: node names as strings, every value a de-duplicated list of strings.

    A bare string dependency (``{"build": "test"}``) means that one task -- not its characters --
    and ``None`` means no dependencies. Anything that cannot be read as a graph is an ``error``
    instead of an exception.
    """
    if not isinstance(tasks_graph, dict):
        return {}, "tasks_graph must be an object mapping each task to a list of its prerequisites."
    graph: Dict[str, List[str]] = {}
    for node, deps in tasks_graph.items():
        if deps is None:
            items: List[Any] = []
        elif isinstance(deps, (str, int, float)) and not isinstance(deps, bool):
            items = [deps]
        elif isinstance(deps, (list, tuple, set, frozenset)):
            items = list(deps)
        else:
            return {}, f"Prerequisites of task '{node}' must be a list of task names."
        seen: List[str] = []
        for d in items:
            name = str(d)
            if name not in seen:
                seen.append(name)
        graph[str(node)] = seen
    return graph, None


def _all_nodes(graph: Dict[str, List[str]]) -> Set[str]:
    nodes: Set[str] = set(graph.keys())
    for deps in graph.values():
        nodes.update(deps)
    return nodes


class WorkflowDependencyValidator:
    """DAG validator for workflow task dependencies, cycle detection, topological ordering, and critical path analysis.

    Bugs fixed (v2):
    - detect_cycles now uses an iterative DFS instead of a recursive one, so a
      3,000-task linear chain no longer triggers Python's RecursionError.

    Hardening (v3): malformed input (None, strings for lists, non-dict tasks, mixed key types,
    NaN/negative durations, an empty graph) returns a result with an ``error`` instead of raising;
    topological ordering is linear rather than quadratic.
    """

    @staticmethod
    def detect_cycles(tasks_graph: Dict[str, List[str]]) -> Dict[str, Any]:
        """Validate a task dependency graph where tasks_graph[task] = [prerequisite_tasks].

        Uses an iterative DFS with a three-colour (WHITE=0, GREY=1, BLACK=2)
        node-state scheme to detect back-edges (cycles) without recursion.
        Safe for graphs with tens of thousands of nodes. A graph that cannot be read is reported
        ``workflow_valid: False`` with an ``error`` -- never as valid.
        """
        graph, error = _normalize_graph(tasks_graph)
        if error:
            return {"workflow_valid": False, "circular_dependencies_detected": False, "cycles": [], "error": error}
        tasks_graph = graph
        all_nodes = _all_nodes(graph)

        # 0 = unvisited, 1 = on current path (grey), 2 = finished (black)
        state: Dict[str, int] = {n: 0 for n in all_nodes}
        cycles: List[List[str]] = []

        for start_node in sorted(all_nodes):
            if state[start_node] != 0:
                continue

            # Each stack frame is (node, iterator-over-children)
            # 'path' mirrors the current DFS path so we can record cycles.
            path: List[str] = [start_node]
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
                    child_iters.pop()
                    continue

                child_state = state.get(child, 0)
                if child_state == 1:
                    # Back-edge -> cycle
                    idx = path.index(child)
                    cycles.append(path[idx:] + [child])
                elif child_state == 0:
                    state[child] = 1
                    path.append(child)
                    child_iters.append(iter(tasks_graph.get(child, [])))
                # child_state == 2 -> already fully explored, skip

        return {
            "workflow_valid": len(cycles) == 0,
            "circular_dependencies_detected": len(cycles) > 0,
            "cycles": cycles,
        }

    @classmethod
    def topological_sort(cls, tasks_graph: Dict[str, List[str]]) -> Dict[str, Any]:
        """Return a valid linear execution order if the workflow graph is acyclic.

        Tasks come out tier by tier (everything whose prerequisites are done), each tier sorted by
        name, so the order is deterministic.
        """
        cycle_check = cls.detect_cycles(tasks_graph)
        if cycle_check.get("error"):
            return {"valid": False, "order": [], "cycles": [], "error": cycle_check["error"]}
        if cycle_check["circular_dependencies_detected"]:
            return {
                "valid": False,
                "order": [],
                "cycles": cycle_check["cycles"],
            }

        graph, _ = _normalize_graph(tasks_graph)
        nodes = _all_nodes(graph)
        waiting: Dict[str, int] = {n: len(graph.get(n, [])) for n in nodes}
        dependents: Dict[str, List[str]] = defaultdict(list)
        for node, deps in graph.items():
            for d in deps:
                dependents[d].append(node)

        order: List[str] = []
        ready = sorted(n for n, c in waiting.items() if c == 0)
        while ready:
            order.extend(ready)
            released: List[str] = []
            for n in ready:
                for child in dependents.get(n, []):
                    waiting[child] -= 1
                    if waiting[child] == 0:
                        released.append(child)
            ready = sorted(released)

        if len(order) != len(nodes):
            return {"valid": False, "order": [], "error": "Unable to resolve remaining dependencies."}
        return {"valid": True, "order": order, "cycles": []}

    @classmethod
    def get_parallel_execution_tiers(cls, tasks_graph: Dict[str, List[str]]) -> Dict[str, Any]:
        """Partition DAG into sequential stages/tiers where all tasks in each tier can execute in parallel."""
        topo = cls.topological_sort(tasks_graph)
        if not topo["valid"]:
            result: Dict[str, Any] = {
                "valid": False, "tiers": [], "cycles": topo.get("cycles", []),
                "error": topo.get("error") or "Graph has cycles; cannot build parallel tiers.",
            }
            return result

        graph, _ = _normalize_graph(tasks_graph)
        node_tier: Dict[str, int] = {}
        for node in topo["order"]:
            prereqs = graph.get(node, [])
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
        """Identify critical path duration and bottleneck tasks in the DAG.

        A task missing from ``task_durations`` counts 1.0. A duration that is not a finite number
        >= 0 (NaN, negative, text) makes the result invalid rather than a nonsense total.
        """
        topo = cls.topological_sort(tasks_graph)
        if not topo["valid"]:
            return {
                "valid": False, "critical_path": [], "critical_duration": 0.0,
                "cycles": topo.get("cycles", []),
                "error": topo.get("error") or "Graph has cycles; no critical path exists.",
            }

        graph, _ = _normalize_graph(tasks_graph)
        all_nodes = topo["order"]
        durations: Dict[str, float] = {n: 1.0 for n in all_nodes}
        if task_durations is not None:
            if not isinstance(task_durations, dict):
                return {"valid": False, "critical_path": [], "critical_duration": 0.0,
                        "error": "task_durations must be an object mapping task -> duration."}
            for name, raw in task_durations.items():
                try:
                    value = float(raw)
                except (TypeError, ValueError, OverflowError):
                    value = math.nan
                if isinstance(raw, bool) or not math.isfinite(value) or value < 0:
                    return {"valid": False, "critical_path": [], "critical_duration": 0.0,
                            "error": f"Duration of task '{name}' must be a finite number >= 0."}
                durations[str(name)] = value

        if not all_nodes:
            return {"valid": True, "critical_duration": 0.0, "critical_path": [], "terminal_task": None}

        earliest_start: Dict[str, float] = {n: 0.0 for n in all_nodes}
        pred_on_longest: Dict[str, Optional[str]] = {n: None for n in all_nodes}

        for n in all_nodes:
            prereqs = graph.get(n, [])
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
        """Validate workflow structure: tasks, ids, prerequisites that exist, and cycles.

        Deadlines are *not* checked (``deadlines_checked`` is False): a spec can be reported valid
        and still be unable to meet a deadline.
        """
        if not isinstance(workflow_spec, dict):
            return {"valid": False, "errors": ["Workflow specification must be an object with a 'tasks' list."]}
        tasks = workflow_spec.get("tasks", [])
        if not tasks:
            return {"valid": False, "errors": ["Workflow specification contains no tasks."]}
        if not isinstance(tasks, (list, tuple)):
            return {"valid": False, "errors": ["'tasks' must be a list of task objects."]}

        errors: List[str] = []
        task_ids: Set[str] = set()
        graph: Dict[str, List[str]] = {}

        for position, t in enumerate(tasks):
            if not isinstance(t, dict):
                errors.append(f"Task #{position + 1} is not an object")
                continue
            raw_id = t.get("id") or t.get("name")
            if raw_id is None or isinstance(raw_id, (list, dict, set)) or not str(raw_id).strip():
                errors.append("Task found without an 'id' or 'name'")
                continue
            tid = str(raw_id)
            if tid in task_ids:
                errors.append(f"Duplicate task id: '{tid}'")
            task_ids.add(tid)
            # "depends_on" and "deps" are the spellings callers actually use for the same field.
            raw_deps = (
                t.get("dependencies") or t.get("prerequisites") or t.get("depends_on") or t.get("deps") or []
            )
            norm, dep_error = _normalize_graph({tid: raw_deps})
            if dep_error:
                errors.append(dep_error)
                continue
            graph[tid] = norm[tid]

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
            "deadlines_checked": False,
        }
