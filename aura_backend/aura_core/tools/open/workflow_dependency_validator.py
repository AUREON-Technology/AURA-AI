"""Superseded -- deliberately NOT registered as a tool (ADR-036). The platform already has
`aura_core/domain/planning.py` (`Plan._assert_acyclic`, `Plan.ready_steps`), which the planning
engine already uses to reject cyclic plans and order steps. This class's recursive DFS also raises
`RecursionError` on a dependency chain a few thousand long.

Kept for reference only: nothing under ``aura_core`` may import it
(``tests/architecture/test_superseded_open_tools_unused.py``).
"""

from typing import Any, Dict, List


class WorkflowDependencyValidator:
    """DAG validator for workflow task dependencies, cycle detection, and topological ordering."""

    @staticmethod
    def detect_cycles(tasks_graph: Dict[str, List[str]]) -> Dict[str, Any]:
        """
        Validate a task dependency graph where tasks_graph[task] = [prerequisite_tasks].
        Detects cycles using three-color DFS (0=unvisited, 1=visiting, 2=visited).
        """
        all_nodes = set(tasks_graph.keys())
        for deps in tasks_graph.values():
            all_nodes.update(deps)

        state: Dict[str, int] = {node: 0 for node in all_nodes}
        cycles: List[List[str]] = []
        stack: List[str] = []

        def dfs(node: str) -> None:
            state[node] = 1
            stack.append(node)
            for dep in tasks_graph.get(node, []):
                if state.get(dep, 0) == 1:
                    idx = stack.index(dep)
                    cycles.append(stack[idx:] + [dep])
                elif state.get(dep, 0) == 0:
                    dfs(dep)
            stack.pop()
            state[node] = 2

        for node in sorted(all_nodes):
            if state[node] == 0:
                dfs(node)

        return {
            "workflow_valid": len(cycles) == 0,
            "circular_dependencies_detected": len(cycles) > 0,
            "cycles": cycles,
        }

    @classmethod
    def topological_sort(cls, tasks_graph: Dict[str, List[str]]) -> Dict[str, Any]:
        """Return a valid execution order if the workflow graph is acyclic."""
        cycle_check = cls.detect_cycles(tasks_graph)
        if cycle_check["circular_dependencies_detected"]:
            return {
                "valid": False,
                "order": [],
                "cycles": cycle_check["cycles"],
            }

        all_nodes = set(tasks_graph.keys())
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